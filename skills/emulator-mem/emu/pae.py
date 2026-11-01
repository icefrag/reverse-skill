"""pae.py — 雷电9 (32-bit PAE guest) 宿主侧地址翻译引擎.

层级: CR3 → PDPT(4×qword, 32B) → PD(512) → PT(512) → 4K 页
PAE PTE: qword, PFN=bits[12..35+], NX=bit63, hi32 ∈ {0, 0x80000000}

核心问题: 宿主中客户机 RAM 碎片化, phys→host 无先验.
解法: 表页物理相邻分配 → 每簇一个 δ=host-phys; 用 PD/PT 条目互证求解 δ.

用法:
    pae = Pae()            # 自动发现 Ld9BoxHeadless 进程
    pae.build()            # census + delta 求解 + 块表
    pae.phys2host(phys)    # 物理地址 → 宿主地址
    pae.invert(host)       # 宿主数据地址 → [VA 候选(含完整链)]
    pae.va2host(va, va_rec)  # VA → 宿主 (用 invert 给出的链)
"""
import ctypes
import struct
import sys
import time

import numpy as np
import psutil

sys.path.insert(0, '.')
try:
    from emu.ldmem import open_vm, read
except ImportError:                     # 直接以文件方式运行时
    from ldmem import open_vm, read

CHUNK = 64 * 1024 * 1024
PFN_CAP = 16 * 2**30 // 4096
PFN_MASK = np.uint64(0x000FFFFFFFFFF000)
PS_BIT = np.uint64(0x80)          # PD 条目 PS=1 → 2MB 大页, 非 PT 指针
USER_VA_MAX = 0xC0000000


class MBI(ctypes.Structure):
    _fields_ = [('BaseAddress', ctypes.c_uint64), ('AllocationBase', ctypes.c_uint64),
                ('AllocationProtect', ctypes.c_uint32), ('_a1', ctypes.c_uint32),
                ('RegionSize', ctypes.c_uint64),
                ('State', ctypes.c_uint32), ('Protect', ctypes.c_uint32),
                ('Type', ctypes.c_uint32), ('_a2', ctypes.c_uint32)]


def find_vm_pid():
    best = None
    for p in psutil.process_iter(['name', 'memory_info']):
        if 'ld9boxheadless' in (p.info['name'] or '').lower():
            mi = p.info['memory_info']
            if best is None or mi.rss > best[1]:
                best = (p.pid, mi.rss)
    if not best:
        raise RuntimeError('Ld9BoxHeadless 未运行')
    return best[0]


class Pae:
    def __init__(self, pid=None):
        self.pid = pid or find_vm_pid()
        self.h = open_vm(self.pid)
        self.regions = self._regions()
        self.chunks = []          # [(phys_lo, phys_hi, delta)]
        self.hosts = None         # census 表页 (sorted)
        self.pfn_all = None       # inverted index
        self.idx_all = None
        self.pg_all = None

    # ---------- 基础 ----------
    def _regions(self, min_size=1024 * 1024):
        out, addr = [], 0
        while addr < 0x7FFFFFFEFFFF:
            mbi = MBI()
            if not k32_virtualquery(self.h, addr, mbi):
                break
            if (mbi.State == 0x1000 and mbi.Type == 0x20000
                    and mbi.RegionSize >= min_size and (mbi.Protect & 0xFF) != 0x01):
                out.append((mbi.BaseAddress, mbi.RegionSize))
            addr = mbi.BaseAddress + mbi.RegionSize
        return out

    def read(self, addr, size):
        d, g = read(self.h, addr, size)
        return d if d and g == size else None

    def valid_entries(self, a):
        return ((a & 1) == 1) & (((a >> 32) & np.uint64(0x7FFFFFFF)) == 0) \
            & ((a & np.uint64(0xF00)) == 0) \
            & (((a & PFN_MASK) >> 12) < PFN_CAP)

    # ---------- census ----------
    def census(self):
        hosts, pfns, idxs = [], [], []
        t0 = time.time()
        for ri, (base, size) in enumerate(self.regions):
            off = 0
            while off < size:
                n = min(CHUNK, size - off)
                d = self.read(base + off, n)
                if not d:
                    off += CHUNK
                    continue
                a = np.frombuffer(d, dtype='<u8')
                pages = len(a) // 512
                if pages:
                    a2 = a[:pages * 512].reshape(pages, 512)
                    v = self.valid_entries(a2)
                    nz = a2 != 0
                    nzn = nz.sum(axis=1)
                    good = (v == nz).all(axis=1) & (nzn >= 3)
                    for i in np.nonzero(good)[0]:
                        hosts.append(base + off + int(i) * 4096)
                        row = a2[int(i)]
                        m = np.nonzero(row)[0]
                        pfns.append(((row[m] & PFN_MASK) >> 12).astype('<u8'))
                        idxs.append(m.astype('<u8'))
                off += CHUNK
        self.hosts = np.array(hosts, dtype=np.uint64)
        self.pfn_all = np.concatenate(pfns).astype(np.uint64)
        self.idx_all = np.concatenate(idxs).astype(np.uint64)
        self.pg_all = np.repeat(np.arange(len(hosts), dtype=np.uint64),
                                [len(p) for p in pfns])
        order = np.argsort(self.pfn_all)
        self.pfn_all, self.idx_all, self.pg_all = \
            self.pfn_all[order], self.idx_all[order], self.pg_all[order]
        print(f'census: {len(hosts)} table pages ({time.time()-t0:.0f}s)')
        return len(hosts)

    # ---------- δ 求解 ----------
    def _page_targets(self, pi, cap=64):
        m = self.pg_all == pi
        return self.pfn_all[m][:cap].astype(np.uint64)

    def solve_delta(self):
        """δ 求解: 只有 PD 页(条目指向 PT=表页)可互证. 真 δ 下目标命中率≈100%.

        PD 解出后, 其所在块(物理相邻分配)的 PT/数据页用同一 δ 由块表覆盖."""
        t0 = time.time()
        n = len(self.hosts)
        nz_per_pg = np.bincount(self.pg_all.astype(np.int64), minlength=n)
        pfn_phys = (self.pfn_all << 12).astype(np.uint64)
        pg_i = self.pg_all.astype(np.int64)
        solved = {}
        dead = set()
        pair_h, pair_p = [], []   # 表页 phys↔host 页对

        def try_page(pi, seed_hosts, probe=False):
            H = int(self.hosts[pi])
            tgt = self._page_targets(pi, cap=8 if probe else 64)
            if len(tgt) < 8:
                return None
            phys = (tgt << 12).astype(np.int64)
            cand = np.unique(seed_hosts.astype(np.int64)[:, None] - phys[None, :])
            if probe:
                cand = cand[-1200:]
            else:
                cand = cand[-4000:]
            best, score = None, 0
            for d in cand:
                if d == 0:
                    continue
                hh = (phys + d).astype(np.uint64)
                j = np.clip(np.searchsorted(self.hosts, hh), 0, n - 1)
                s = int((self.hosts[j] == hh).sum())
                if s > score:
                    best, score = int(d), s
            if best is None:
                return None
            if probe:
                return best if score >= 6 else None
            # 全量: 真 δ 下目标(PT 页)应大部分命中普查集合
            return (best, score) if score >= max(8, (len(tgt) * 6) // 10) else None

        def global_apply(d_new, unsolved):
            hh = pfn_phys + d_new
            j = np.clip(np.searchsorted(self.hosts, hh), 0, n - 1)
            hit = self.hosts[j] == hh
            sc = np.bincount(pg_i[hit], minlength=n)
            cand = np.array(sorted(unsolved), dtype=np.int64)
            ok = sc[cand] >= np.maximum(8, (nz_per_pg[cand] * 6) // 10)
            absorbed = cand[ok]
            for pi in absorbed:
                solved[int(pi)] = d_new
            # 记录 phys↔host 页对: 命中的 PT 目标页 + PD 页自身
            pair_h.extend(self.hosts[j][hit].tolist())
            pair_p.extend(hh[hit].tolist())
            if len(absorbed):
                ah = self.hosts[absorbed]
                pair_h.extend(ah.tolist())
                pair_p.extend((ah - np.uint64(d_new & 0xFFFFFFFFFFFFFFFF)).tolist())
            return absorbed

        unsolved = set(range(n))
        it = 0
        while unsolved and it < n:
            it += 1
            cand = np.array(sorted(unsolved), dtype=np.int64)
            pi = int(cand[np.argmax(nz_per_pg[cand])])
            H = int(self.hosts[pi])
            # 两段式: 紧邻种子(±8MB)廉价探针 → 通过才做全量验证
            lo = np.searchsorted(self.hosts, np.uint64(max(0, H - 8 * 2**20)))
            hi = np.searchsorted(self.hosts, np.uint64(H + 8 * 2**20))
            seeds = self.hosts[lo:hi]
            if len(seeds) < 4 or not try_page(pi, seeds, probe=True):
                if it % 500 == 0:
                    print(f'  ...probing {it} left={len(unsolved)} ({time.time()-t0:.0f}s)')
                unsolved.discard(pi)
                dead.add(pi)
                continue
            r = try_page(pi, self.hosts[lo:hi])
            if not r:
                unsolved.discard(pi)
                dead.add(pi)
                continue
            d_new = r[0]
            absorbed = global_apply(d_new, unsolved)
            for p in absorbed:
                unsolved.discard(int(p))
            print(f'  iter{it}: seed@0x{H:X} delta=0x{d_new & 0xFFFFFFFFFFFFFFFF:X} '
                  f'absorbed={len(absorbed)} left={len(unsolved)} ({time.time()-t0:.0f}s)')
            if len(absorbed) == 0:
                unsolved.discard(pi)
                solved[pi] = d_new
        print(f'solved PDs: {sum(1 for v in solved.values() if v)}  '
              f'deltas={len(set(v for v in solved.values() if v))}  '
              f'dead={len(dead)} ({time.time()-t0:.0f}s)')
        # 表页映射字典: phys_page ↔ host_page (去重+清洗)
        P = np.array(pair_p, dtype=np.uint64)
        H = np.array(pair_h, dtype=np.uint64)
        m = P != H                       # 删自环 (数据页垃圾)
        P, H = P[m], H[m]
        dd = (H - P).astype(np.int64)
        ud, cnt = np.unique(dd, return_counts=True)
        keep_d = set(int(x) for x in ud[cnt >= 3])   # 只留主流 δ 簇
        m2 = np.array([int(x) in keep_d for x in dd[:1000]].__class__([True]*0) if False else
                      [int(x) in keep_d for x in dd])
        P, H = P[m2], H[m2]
        ph = np.unique(np.stack([P, H]), axis=1)
        order = np.argsort(ph[0])
        self.tbl_phys = ph[0][order]
        self.tbl_host = ph[1][order]
        if hasattr(self, '_th'):
            del self._th
        print(f'table page pairs: {len(self.tbl_phys)} (deltas kept={len(keep_d)})')
        self._build_chunks({int(self.hosts[pi]): d for pi, d in solved.items() if d})
        return solved

    def _build_chunks(self, solved):
        """solved: {page_host: delta} → 区域级多数票: 每个已提交区域一个 δ."""
        by_region = {}
        for h, d in solved.items():
            for ri, (base, size) in enumerate(self.regions):
                if base <= h < base + size:
                    by_region.setdefault(ri, {}).setdefault(d, 0)
                    by_region[ri][d] += 1
                    break
        self.chunks = []
        for ri, votes in by_region.items():
            d, cnt = max(votes.items(), key=lambda kv: kv[1])
            total = sum(votes.values())
            if cnt < 2:
                continue
            base, size = self.regions[ri]
            self.chunks.append((base, base + size, d))
            print(f'  region[{ri}] 0x{base:X}+{size//2**20}MB delta=0x{d & 0xFFFFFFFFFFFFFFFF:X} '
                  f'votes={cnt}/{total}')
        self.chunks.sort(key=lambda c: c[1] - c[0], reverse=True)

    # ---------- 翻译 ----------
    def phys2host_tbl(self, phys):
        """表页映射查询 (solve_delta 产出的 phys↔host 对)."""
        if not hasattr(self, 'tbl_phys') or self.tbl_phys is None:
            return None
        p = int(phys) & 0xFFFFFFFFFFFFF000
        j = np.searchsorted(self.tbl_phys, np.uint64(p))
        if j < len(self.tbl_phys) and int(self.tbl_phys[j]) == p:
            return int(self.tbl_host[j])
        return None

    def host_phys_cands(self, host):
        """表页宿主页的全部候选物理页 (映射对可能有多解)."""
        if not hasattr(self, 'tbl_phys') or self.tbl_phys is None:
            return []
        if not hasattr(self, '_th'):
            o = np.argsort(self.tbl_host)
            self._th = self.tbl_host[o]
            self._tp = self.tbl_phys[o]
        h = np.uint64(int(host) & 0xFFFFFFFFFFFFF000)
        lo = np.searchsorted(self._th, h, 'left')
        hi = np.searchsorted(self._th, h, 'right')
        return [int(p) for p in self._tp[lo:hi]]

    def host2phys_tbl(self, host):
        """表页反查: 宿主页 → 物理页."""
        if not hasattr(self, 'tbl_phys') or self.tbl_phys is None:
            return None
        if not hasattr(self, '_th_order'):
            o = np.argsort(self.tbl_host)
            self._th = self.tbl_host[o]
            self._tp = self.tbl_phys[o]
        h = int(host) & 0xFFFFFFFFFFFFF000
        j = np.searchsorted(self._th, np.uint64(h))
        if j < len(self._th) and int(self._th[j]) == h:
            return int(self._tp[j])
        return None

    def phys2host(self, phys):
        best = None
        for lo, hi, d in self.chunks:
            if lo <= phys < hi:
                if best is None or (hi - lo) < (best[1] - best[0]):
                    best = (lo, hi, d)
        return phys + best[2] if best else None

    def host2phys(self, host):
        for lo, hi, d in self.chunks:
            if lo + d <= host < hi + d:
                return host - d
        return None

    def pages_with_pfn(self, pfn):
        """含指向 pfn 条目的表页: [(page_host, entry_idx)]"""
        lo = np.searchsorted(self.pfn_all, np.uint64(pfn), 'left')
        hi = np.searchsorted(self.pfn_all, np.uint64(pfn), 'right')
        return [(int(self.hosts[int(self.pg_all[k])]), int(self.idx_all[k]))
                for k in range(lo, hi)]

    def scan_qword_pfn(self, pfn):
        """全内存扫 qword: (q & PFN_MASK)>>12 == pfn 且 present. 返回宿主地址列表."""
        target = (np.uint64(pfn) << 12) & PFN_MASK
        hits = []
        for base, size in self.regions:
            off = 0
            while off < size:
                n = min(CHUNK, size - off)
                d = self.read(base + off, n)
                if not d:
                    off += CHUNK
                    continue
                a = np.frombuffer(d, dtype='<u8')
                m = ((a & PFN_MASK) == target) & ((a & 1) == 1) & \
                    (((a >> 32) & np.uint64(0x7FFFFFFF)) == 0)
                for i in np.nonzero(m)[0]:
                    hits.append(base + off + int(i) * 8)
                off += CHUNK
        return hits

    def build(self, use_saved=None, cache=True):
        """census + δ求解(或载缓存) + 区域多数票块表 + PDPT 索引."""
        import json as _json
        import os as _os
        t0m = time.time()
        cpath = 'pae_cache.npz'
        if cache and _os.path.exists(cpath) \
                and time.time() - _os.path.getmtime(cpath) < 600:
            try:
                z = np.load(cpath)
                if int(z['pid']) == self.pid:
                    self.hosts = z['hosts']
                    self.pfn_all = z['pfn_all']
                    self.idx_all = z['idx_all']
                    self.pg_all = z['pg_all']
                    self.chunks = [(int(a), int(b), int(c)) for a, b, c in z['chunks']]
                    self.pd_idx = z['pd_idx']
                    self.pd_host = z['pd_host']
                    if 'tbl_phys' in z:
                        self.tbl_phys = z['tbl_phys']
                        self.tbl_host = z['tbl_host']
                    print(f'loaded engine cache: {len(self.hosts)} tables, '
                          f'{len(self.chunks)} chunks ({time.time()-t0m:.0f}s)')
                    return self
            except Exception as e:
                print('cache load failed:', e)
        t0m = time.time()
        self.census()
        if use_saved and _os.path.exists(use_saved):
            saved = _json.load(open(use_saved))
            if saved.get('pid') == self.pid:
                solved = {}
                for sp in saved['solved_pages']:
                    h = sp['host']
                    h = int(h, 16) if isinstance(h, str) else int(h)
                    solved[h] = int(sp['delta'])
                print(f'loaded saved deltas: {len(solved)} pages')
                self._build_chunks(solved)
        if not self.chunks:
            solved = self.solve_delta()
        else:
            solved = {}
        self.build_pdpt_index()
        if cache:
            try:
                extra = {}
                if getattr(self, 'tbl_phys', None) is not None:
                    extra['tbl_phys'] = self.tbl_phys
                    extra['tbl_host'] = self.tbl_host
                np.savez(cpath, pid=np.int64(self.pid), hosts=self.hosts,
                         pfn_all=self.pfn_all, idx_all=self.idx_all,
                         pg_all=self.pg_all,
                         chunks=np.array(self.chunks, dtype=np.uint64),
                         pd_idx=self.pd_idx, pd_host=self.pd_host, **extra)
            except Exception as e:
                print('cache save failed:', e)
        return self

    def build_pdpt_index(self):
        """一次全内存扫描, 索引所有指向表页物理帧的 qword (PDPT 条目/直 map 别名)."""
        t0 = time.time()
        tphys = []
        for h in self.hosts:
            p = self.host2phys(int(h))
            if p is not None:
                tphys.append(p >> 12)
        tset = np.unique(np.array(tphys, dtype=np.uint64))
        idx_pfn, idx_host = [], []
        for base, size in self.regions:
            off = 0
            while off < size:
                n = min(CHUNK, size - off)
                d = self.read(base + off, n)
                if not d:
                    off += CHUNK
                    continue
                a = np.frombuffer(d, dtype='<u8')
                m = ((a & 1) == 1) & (((a >> 32) & np.uint64(0x7FFFFFFF)) == 0) \
                    & ((a & np.uint64(0xF00)) == 0)
                cand_pfn = ((a & PFN_MASK) >> 12).astype(np.uint64)
                j = np.clip(np.searchsorted(tset, cand_pfn), 0, len(tset) - 1)
                m &= tset[j] == cand_pfn
                for i in np.nonzero(m)[0]:
                    idx_pfn.append(int(cand_pfn[i]))
                    idx_host.append(base + off + int(i) * 8)
                off += CHUNK
        self.pd_idx = np.array(idx_pfn, dtype=np.uint64)
        order = np.argsort(self.pd_idx)
        self.pd_idx = self.pd_idx[order]
        self.pd_host = np.array(idx_host, dtype=np.uint64)[order]
        print(f'pdpt index: {len(self.pd_idx)} refs ({time.time()-t0:.0f}s)')

    def pages_with_pdptr(self, pfn):
        """指向 pfn 的 qword 宿主地址列表 (PDPT 条目等)."""
        lo = np.searchsorted(self.pd_idx, np.uint64(pfn), 'left')
        hi = np.searchsorted(self.pd_idx, np.uint64(pfn), 'right')
        return [int(self.pd_host[k]) for k in range(lo, hi)]

    def invert(self, host, max_results=8):
        """宿主数据地址 → VA 候选链. 返回 [(va, pdpt_base_host)]"""
        out = []
        phys = self.host2phys(host & ~0xFFF)
        if phys is None:
            return out
        pfn = phys >> 12
        for pt_host, pte_idx in self.pages_with_pfn(pfn):
            pt_phys = self.host2phys(pt_host)
            if pt_phys is None:
                continue
            va = pte_idx << 12 | (host & 0xFFF)
            for pd_host, pde_idx in self.pages_with_pfn(pt_phys >> 12):
                va1 = pde_idx << 21 | va
                if va1 >= USER_VA_MAX:
                    continue          # 内核直 map 别名, 剪枝
                pd_phys = self.host2phys(pd_host)
                if pd_phys is None:
                    continue
                # PDPT 条目: 索引查询 + va2host 回环验证
                for pdpt_e_host in self.pages_with_pdptr(pd_phys >> 12):
                    q = self.read(pdpt_e_host, 8)
                    if not q:
                        continue
                    qv = struct.unpack('<Q', q)[0]
                    if qv & PS_BIT:
                        continue
                    off32 = (pdpt_e_host % 4096) % 32
                    e_idx = off32 // 8
                    va2 = e_idx << 30 | va1
                    if va2 >= USER_VA_MAX:
                        continue
                    pdpt_base = pdpt_e_host - off32
                    got = self.va2host(va2, {'pdpt': pdpt_base})
                    if got is not None and (got & ~0xFFF) == (host & ~0xFFF):
                        out.append((va2, pdpt_base))
                        if len(out) >= max_results:
                            return out
        return out

    def va2host(self, va, cache):
        """VA → 宿主. cache: {'pdpt': base_host, 'pd': {pdkey: host}, 'pt': {ptkey: host}}"""
        pdpt = cache.get('pdpt')
        q = self.read(pdpt, 32)
        if not q or len(q) < 32:
            return None
        e = struct.unpack('<Q', q[8 * ((va >> 30) & 3):][:8])[0]
        if not (e & 1) or (e & PS_BIT):
            return None
        pd_phys = e & 0x000FFFFFFFFFF000
        k1 = (int(va) >> 21) & ~511
        pd_host = cache['pd'].get(k1)
        if pd_host is None:
            pd_host = self.phys2host_tbl(pd_phys) or self.phys2host(pd_phys)
            if pd_host is None or pd_host > 0x7FFFFFFEFFFF:
                return None
            cache['pd'][k1] = pd_host
        d, g = read(self.h, pd_host + 8 * ((va >> 21) & 511), 8)
        if not d or len(d) < 8:
            return None
        e = struct.unpack('<Q', d)[0]
        if not (e & 1) or (e & PS_BIT):
            return None
        pt_phys = e & 0x000FFFFFFFFFF000
        k2 = (int(va) >> 12) & ~511
        pt_host = cache['pt'].get(k2)
        if pt_host is None:
            pt_host = self.phys2host_tbl(pt_phys) or self.phys2host(pt_phys)
            if pt_host is None or pt_host > 0x7FFFFFFEFFFF:
                return None
            cache['pt'][k2] = pt_host
        d, g = read(self.h, pt_host + 8 * ((va >> 12) & 511), 8)
        if not d or len(d) < 8:
            return None
        e = struct.unpack('<Q', d)[0]
        if not (e & 1):
            return None
        page = self.phys2host(e & 0x000FFFFFFFFFF000)
        if page is None or page > 0x7FFFFFFEFFFF:
            return None
        return page + (va & 0xFFF)


k32_virtualquery = None
k32 = ctypes.windll.kernel32


def k32_virtualquery(h, addr, mbi):
    return k32.VirtualQueryEx(h, ctypes.c_uint64(addr), ctypes.byref(mbi), ctypes.sizeof(mbi))
