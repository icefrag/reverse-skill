"""resolve.py v2 — 堆段 δ 直接反解 + 实体 VA (不枚举 PDPT).

核心: PT 页条目给出 (pti → data_φ). 全部 PT 目标合成全局 φ 空间.
真堆 δ 满足: 每个锚点宿主页 - δ ∈ φ 空间. 三次向量化筛选即收敛.
之后回溯: φ → PT(内容匹配) → PD(pages_with_pfn) → PDPT 槽位(pdpt 索引) → VA.
"""
import json
import re
import struct
import sys
import time

import numpy as np
import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent.parent))
from emu.pae import Pae, PFN_MASK, PS_BIT, CHUNK, USER_VA_MAX

sys.path.insert(0, '.')


def telemetry_roles(pae):
    """收集内存里全部遥测 role_id (多角色/残留 JSON 都要试)."""
    pat = re.compile(rb'"role_id":"(\d+)"')
    pat2 = re.compile(rb'"nd_account":"(\d+)"')
    ids, facs = [], []
    for base, size in pae.regions:
        off = 0
        while off < size:
            n = min(CHUNK, size - off)
            d = pae.read(base + off, n)
            if not d:
                off += CHUNK
                continue
            for m in pat.finditer(d):
                v = int(m.group(1))
                if v > 100000 and v not in ids:   # 过滤空值/垃圾
                    ids.append(v)
            if not facs:
                m = pat2.search(d)
                if m:
                    facs.append(int(m.group(1)))
            off += CHUNK
    return ids, (facs[0] if facs else None)


def find_entity(pae, role_id):
    hits = []
    for base, size in pae.regions:
        off = 0
        while off < size:
            n = min(CHUNK, size - off)
            d = pae.read(base + off, n)
            if not d:
                off += CHUNK
                continue
            ai = np.frombuffer(d[:len(d) // 4 * 4], dtype='<i4')
            m = np.nonzero(ai == np.int32(role_id))[0]
            if len(m):
                m2 = m[(m > 0xA1) & (m < len(ai) - 0x30)]
                if len(m2):
                    xa, ya = ai[m2 - 0xA1], ai[m2 - 0xA0]
                    xb, yb = ai[m2 + 0x2B], ai[m2 + 0x2C]
                    ok = ((xa > 0) & (xa < 3000) & (ya > 0) & (ya < 3000)
                          & (xb > 0) & (xb < 3000) & (yb > 0) & (yb < 3000)
                          & (np.abs(xa - xb) <= 2) & (np.abs(ya - yb) <= 2))
                    for i, x, yv in zip(m2[ok], xa[ok], ya[ok]):
                        hits.append((base + off + int(i) * 4, int(x), int(yv)))
            off += CHUNK
    return hits


def anchor_pages(pae, ints):
    pages = set()
    targets = [np.uint32(v) for v in ints if v]
    for base, size in pae.regions:
        off = 0
        while off < size:
            n = min(CHUNK, size - off)
            d = pae.read(base + off, n)
            if not d:
                off += CHUNK
                continue
            a = np.frombuffer(d[:len(d) // 4 * 4], dtype='<u4')
            for t in targets:
                m = np.nonzero(a == t)[0]
                for i in m[:2000]:
                    pages.add((base + off + int(i) * 4) & ~0xFFF)
            off += CHUNK
    return pages


def build_phi_space(pae):
    """PT 页 = 目标多数不在表页集合的 census 页. 返回 (φ_space sorted, pt_page_idx)."""
    n = len(pae.hosts)
    tset = np.unique((pae.tbl_phys >> 12).astype(np.uint64))
    in_tbl = np.zeros(len(pae.pfn_all), dtype=bool)
    j = np.clip(np.searchsorted(tset, pae.pfn_all), 0, len(tset) - 1)
    in_tbl = tset[j] == pae.pfn_all
    pg_i = pae.pg_all.astype(np.int64)
    nz = np.bincount(pg_i, minlength=n)
    tb = np.bincount(pg_i[in_tbl], minlength=n)
    pt_mask = tb < np.maximum(1, (nz * 5) // 10)     # <50% 目标是表页 → PT
    pt_pages = np.nonzero(pt_mask & (nz >= 8))[0]
    m = np.isin(pg_i, pt_pages)
    phi = np.unique((pae.pfn_all[m] << 12).astype(np.uint64))
    print(f'PT pages: {len(pt_pages)}, phi_space: {len(phi)}')
    return phi, pt_pages


def solve_heap_delta(anchor_pages, phi):
    """真堆 δ: 软打分 (命中锚点数最多), 抗锚点漂移."""
    A = sorted(anchor_pages)
    d_c = np.int64(A[0]) - phi.astype(np.int64)
    d_c = np.unique(d_c[d_c > 0])
    scores = np.zeros(len(d_c), dtype=np.int64)
    for a in A:
        phi2 = (np.int64(a) - d_c).astype(np.uint64)
        j = np.clip(np.searchsorted(phi, phi2), 0, len(phi) - 1)
        scores += (phi[j] == phi2)
    k = int(np.argmax(scores))
    return int(d_c[k]), int(scores[k])


def _scan_qword_pfn_user(pae, cand_pfns, regions=None):
    """全内存扫 qword: hi32==0(用户/非NX), present, pfn ∈ cand. 返回 [(host, pfn)]"""
    cands = np.array(sorted(cand_pfns), dtype=np.uint64)
    out = []
    if len(cands) == 0:
        return out
    for base, size in (regions or pae.regions):
        off = 0
        while off < size:
            n = min(CHUNK, size - off)
            d = pae.read(base + off, n)
            if not d:
                off += CHUNK
                continue
            a = np.frombuffer(d[:len(d) // 8 * 8], dtype='<u8')
            m = ((a & 1) == 1) & ((a >> 32) == 0) \
                & ((a & np.uint64(0xF00)) == 0)
            pf = ((a & PFN_MASK) >> 12).astype(np.uint64)
            j = np.clip(np.searchsorted(cands, pf), 0, len(cands) - 1)
            m &= cands[j] == pf
            for i in np.nonzero(m)[0]:
                out.append((base + off + int(i) * 8, int(pf[i])))
            off += CHUNK
    return out


def backtrack_va(pae, phi_page, phi, expect_host):
    """φ(数据页) → [(va, pdpt_base)]. expect_host = 数据页当前宿主 (回环目标).

    PT 物理 = 宿主−δ 枚举; PD/PDPT 用内容扫描定位 (指向 PT/PD 物理帧的
    用户半区 qword = 页表条目本体). pass2 合并为一次扫描. 最后回环验证."""
    deltas = [int(x) for x in np.unique((pae.tbl_host - pae.tbl_phys).astype(np.int64))
              if x != 0]
    out = []
    pfn = phi_page >> 12
    stat = {'pt': 0, 'pass1': 0, 'pd': 0, 'pass2': 0, 'chk': 0, 'rt': 0}

    # ---- pass1: 所有 PT 候选合并, 找指向它们的 PD 条目 ----
    pt_infos = []          # (pt_host, pti)
    pt_pfn_cands = set()   # PT 物理 pfn 候选
    for pt_host0, pti in pae.pages_with_pfn(pfn):
        stat['pt'] += 1
        pt_infos.append((pt_host0 & ~0xFFF, pti))
        H = pt_host0 & ~0xFFF
        pt_pfn_cands |= {np.uint64(H - d_t) >> 12 for d_t in deltas
                         if 0 <= H - d_t <= 0xFFFFF000}
        pt_pfn_cands |= {np.uint64(p >> 12) for p in pae.host_phys_cands(H)}
    hits1 = _scan_qword_pfn_user(pae, sorted(pt_pfn_cands))
    stat['pass1'] = len(hits1)

    # pd 元组: (H_pd, pdi, pt_phys, pt_host, pti)
    pds = []
    pd_phys_cands = {}     # H_pd → 候选物理集合
    for w, pf_pt in hits1:
        H_pd = w & ~0xFFF
        pdi = (w % 4096) // 8
        pt_phys = pf_pt << 12
        for pt_host, pti in pt_infos:
            # 该 PT 候选的物理必须与 pt_phys 一致 (宿主−δ)
            ok = any(0 <= pt_host - int(d) <= 0xFFFFF000 and pt_host - int(d) == pt_phys
                     for d in deltas) or pt_phys in pae.host_phys_cands(pt_host)
            if ok:
                pds.append((H_pd, pdi, pt_phys, pt_host, pti))
                if H_pd not in pd_phys_cands:
                    pd_phys_cands[H_pd] = {np.uint64(H_pd - d_p) >> 12 for d_p in deltas
                                           if 0 <= H_pd - d_p <= 0xFFFFF000}
                    pd_phys_cands[H_pd] |= {np.uint64(p >> 12)
                                            for p in pae.host_phys_cands(H_pd)}
    stat['pd'] = len(pds)

    # ---- pass2: 一次扫描全部 PD 物理候选 ----
    union = sorted({p for s in pd_phys_cands.values() for p in s})
    hits2 = _scan_qword_pfn_user(pae, union)
    stat['pass2'] = len(hits2)
    for w2, pf_pd in hits2:
        for (H_pd, pdi, pt_phys, pt_host, pti) in pds:
            if pf_pd not in pd_phys_cands.get(H_pd, ()):
                continue
            slot = (w2 % 4096) % 32 // 8
            base = w2 - (w2 % 4096) % 32
            va = slot << 30 | pdi << 21 | pti << 12
            if va >= USER_VA_MAX:
                continue
            q = pae.read(base, 32)
            if not q or len(q) < 32:
                continue
            e = struct.unpack('<Q', q[8 * slot:][:8])[0]
            if not (e & 1) or ((e & PFN_MASK) >> 12) != pf_pd:
                continue
            stat['chk'] += 1
            cache = {'pdpt': base,
                     'pd': {(va >> 21) & ~511: H_pd},
                     'pt': {(va >> 12) & ~511: pt_host}}
            got = pae.va2host(va | (phi_page & 0xFFF), cache)
            if got is not None and (got & ~0xFFF) == (expect_host & ~0xFFF):
                stat['rt'] += 1
                out.append((va | (phi_page & 0xFFF), base, H_pd, pt_host))
    print(f'backtrack stat: {stat}')
    seen, res = set(), []
    for va, b, hpd, hpt in out:
        if (va, b) not in seen:
            seen.add((va, b))
            res.append((va, b, hpd, hpt))
    return res, pds, pd_phys_cands, deltas


def pick_game_pid():
    """多实例时按锚点探测选游戏所在 VM (RSS 启发式不可靠)."""
    import re as _re
    import psutil
    pids = [p.pid for p in psutil.process_iter(['name'])
            if 'ld9boxheadless' in (p.info['name'] or '').lower()]
    if len(pids) <= 1:
        return pids[0] if pids else None
    lk = json.load(open('last_known.json'))
    rid = lk['anchors'].get('ANCHOR_ROLE_ID')
    pat = _re.compile(rb'"role_id":"(\d+)"')
    pat_acc = _re.compile(rb'"nd_account":"(\d+)"')
    best = None
    best_tel = (None, None)
    for pid in pids:
        pae = Pae(pid=pid)
        tel = 0
        cnt = 0
        for base, size in pae.regions:
            off = 0
            while off < size:
                n = min(CHUNK, size - off)
                d = pae.read(base + off, n)
                if not d:
                    off += CHUNK
                    continue
                if not tel:
                    m = pat.search(d)
                    if m:
                        tel = int(m.group(1))
                if rid:
                    cnt += int((np.frombuffer(d[:len(d) // 4 * 4], dtype='<u4')
                                == np.uint32(rid)).sum())
                if tel and cnt:
                    break
                off += CHUNK
            if tel and cnt:
                break
        print(f'  probe pid={pid}: telemetry={tel} u32_hits={cnt}')
        if tel or cnt:
            best = pid
            best_tel = (tel, None)
            if tel and cnt:
                break
    globals()['PICKED_TEL'] = best_tel
    return best


DEBUG_SCORE = False


def main():
    t0 = time.time()
    pid = pick_game_pid()
    if not pid:
        print('!! 未找到含游戏锚点的模拟器实例')
        return
    pae = Pae(pid=pid)
    print(f'vm pid={pae.pid}')
    pae.build(cache=True)
    if getattr(pae, 'tbl_phys', None) is None or len(pae.tbl_phys) == 0:
        print('!! 表页映射缺失, 重新求解')
        pae.census()
        pae.solve_delta()
        pae.build_pdpt_index()

    ids, acc = telemetry_roles(pae)
    if globals().get('PICKED_TEL', (None, None))[0] and             PICKED_TEL[0] not in ids:
        ids.append(PICKED_TEL[0])
    lk = json.load(open('last_known.json'))
    cached = lk['anchors'].get('ANCHOR_ROLE_ID')
    if cached and cached not in ids:
        ids.append(cached)
    print(f'telemetry roles: {ids} account={acc}')
    if not ids:
        print('!! 无锚点可用')
        return
    # 逐角色试: 谁有场景实体谁就是活角色
    ent_host = ex = ey = None
    rid = None
    for r in ids:
        ents = find_entity(pae, r)
        if ents:
            rid = r
            ent_host, ex, ey = ents[0]
            print(f'活角色 role_id={rid} entity={hex(ent_host)} ({ex},{ey})')
            break
    if rid is None:
        print('!! 所有已知角色都没有场景实体 (不在游戏内?)')
        return

    anchors = anchor_pages(pae, [rid, acc])
    anchors.add(ent_host & ~0xFFF)
    print(f'anchor pages: {len(anchors)}')

    phi, pt_pages = build_phi_space(pae)
    d, hits = solve_heap_delta(anchors, phi)
    print(f'heap_delta=0x{d:X} anchor_hits={hits}/{len(anchors)} ({time.time()-t0:.0f}s)')
    if not d or hits < max(8, len(anchors) * 4 // 10):
        print('!! δ 未收敛')
        return

    # 堆段块表 (插到最前, 避免被大区域票选 δ 遮蔽)
    ap = np.array(sorted(anchors), dtype=np.uint64)
    pmin, pmax = (ap - np.uint64(d)).min(), (ap - np.uint64(d)).max()
    pae.chunks.insert(0, (int(pmin), int(pmax) + 0x1000, d))

    # ---- 反查: 实体优先, 锚点兜底 (同一地址空间) ----
    def try_backtrack(host):
        phi_h = np.uint64((host & ~0xFFF) - d)
        if not pae.pages_with_pfn(int(phi_h) >> 12):
            return None
        va_list, pds, pd_cands, deltas = backtrack_va(pae, int(phi_h), phi, host)
        if not va_list:
            return None
        return va_list, pds, pd_cands, deltas, host

    _r = try_backtrack(ent_host)
    vas, pds_ev, pd_cands_ev, deltas_ev, host_used = _r if _r else ([], None, None, None, ent_host)
    base_found = None
    if vas:
        base_found = 'entity'
    else:
        print('entity φ 无 PT (页漂移), 用锚点页反查地址空间 ...')
        for h in sorted(anchors)[:12]:
            _r = try_backtrack(h)
            if _r and _r[0]:
                vas, pds_ev, pd_cands_ev, deltas_ev, host_used = _r
                base_found = 'anchor'
                break
        if vas:
            # 地址空间已定位, 重扫实体再试一次
            ents2 = find_entity(pae, rid)
            for h2, x2, y2 in ents2:
                _r2 = try_backtrack(h2)
                if _r2 and _r2[0]:
                    vas, pds_ev, pd_cands_ev, deltas_ev, host_used = _r2
                    ent_host, ex, ey = h2, x2, y2
                    base_found = 'entity'
                    break
    print(f'backtrack via: {base_found}, candidates={len(vas) if vas else 0}')
    if not vas:
        print('!! 反查全部失败')
        return

    # ---- 去伪: pdpt 结构 + 指针翻译投票 ----
    ents_raw = pae.read(ent_host, 0x400)
    ptrs = []
    for o in range(0, 0x400, 4):
        v = struct.unpack('<I', ents_raw[o:o + 4])[0]
        if 0x08048000 <= v < USER_VA_MAX and v % 4 == 0:
            ptrs.append(v)
    print(f'ptr-like fields: {len(ptrs)}')
    host_pages = np.uint64(pae.hosts)

    def score(base, pds, pd_phys_cands, deltas):
        """v5: 内核区 + 连续PD对签名 + 槽位命中 + 翻译投票."""
        if not pds:
            return -1
        q = pae.read(base, 32)
        if not q or len(q) < 32:
            return -1
        win = [struct.unpack('<Q', q[8 * i:8 * i + 8])[0] for i in range(4)]
        valid = []
        for e in win:
            if e and (e & 1) and (e >> 32 == 0) and not (e & PS_BIT):
                valid.append(int(e) & 0xFFFFFFFFFFFFF000)
            else:
                valid.append(None)
        if sum(1 for v in valid if v) < 3:
            return -1
        # 基地不在游戏堆宿主范围 (真 PDPT 在内核区)
        if pmin <= base - 0x7FFF0000 <= (pmax - 0x7FFF0000) or base > 0x140000000:
            pass
        if base > 0x134000000 and base < 0x140000000:
            return -1                      # 大区域尾部=堆数据区
        # 连续对签名: 内核成批分配 PD → 相邻槽位物理相邻
        consec = 0
        vv = [v for v in valid if v]
        for a, b2 in zip(vv, vv[1:]):
            if 0 < abs(a - b2) <= 0x4000:
                consec += 1
        # 槽位命中 PD 候选
        struct_hits = 0
        for pd_phys in valid:
            if pd_phys is None:
                continue
            for t in pds:
                if (t[0] - pd_phys) in deltas or pd_phys >> 12 in pd_phys_cands.get(t[0], ()):
                    struct_hits += 1
                    break
        # 该 base 的 slot2 候选翻译投票
        cands = [t for t in grouped.get(base, []) if (t[0] >> 30) == 2]
        if not cands:
            return 0
        H_pd, pdi, pt_phys, pt_host, pti = pds[0]
        dcl = pt_host - pt_phys
        pd = pae.read(H_pd, 4096)
        if not pd:
            return -1
        pda = np.frombuffer(pd, dtype='<u8')
        votes = 0
        for p in ptrs:
            if (p >> 30) != 2:
                continue
            pdi_p = (p >> 21) & 511
            e_pd = int(pda[pdi_p])
            if not (e_pd & 1) or (e_pd >> 32 != 0) or (e_pd & PS_BIT):
                continue
            pt2_phys = e_pd & PFN_MASK
            pt2_host = pae.phys2host_tbl(pt2_phys)
            if pt2_host is None:
                pt2_host = int(pt2_phys) + int(dcl)
            if not (0 < pt2_host < 0x7FFFFFFEFFFF):
                continue
            pt2 = pae.read(pt2_host, 4096)
            if not pt2:
                continue
            pt2a = np.frombuffer(pt2, dtype='<u8')
            if int(((pt2a & 1) == 1).sum()) < 8:
                continue
            e_pt = int(pt2a[(p >> 12) & 511])
            if not (e_pt & 1) or (e_pt >> 32 != 0):
                continue
            phi_leaf = e_pt & PFN_MASK
            if pmin <= int(phi_leaf) <= int(pmax) + 0x1000:
                votes += 1
        return (struct_hits + consec * 2, votes)

    json.dump([[int(v), int(b), int(hpd), int(hpt)] for v, b, hpd, hpt in vas],
              open('va_cands.json', 'w'))
    grouped = {}
    for va, base, hpd, hpt in vas:
        grouped.setdefault(base, []).append((va, hpd, hpt))
    scored = sorted(((score(b, pds_ev, pd_cands_ev, deltas_ev), b, vs)
                     for b, vs in grouped.items()),
                    key=lambda t: (t[0][0] if isinstance(t[0], tuple) else t[0],
                                   t[0][1] if isinstance(t[0], tuple) else 0),
                    reverse=True)
    if scored and scored[0][0] == 0:
        globals()['DEBUG_SCORE'] = True
        print(f'DEBUG score for top base 0x{scored[0][1]:X}:')
        scored = sorted(((score(b, pds_ev, pd_cands_ev, deltas_ev), b, vs)
                         for b, vs in list(grouped.items())[:3]),
                        key=lambda t: -t[0])
        globals()['DEBUG_SCORE'] = False
    result = None
    for sc, base, vs in scored[:3]:
        print(f'  pdpt@0x{base:X} score={sc} va_n={len(vs)} '
              f'vas={sorted(set(hex(t[0]) for t in vs))[:6]}')
    if scored and isinstance(scored[0][0], tuple) and scored[0][0][0] >= 4:
        sc, base, vs = scored[0]
        result = {'pid': pae.pid, 'pdpt_host': base, 'heap_delta': d,
                  'role_id': rid, 'account': acc,
                  'entity_host': ent_host, 'entity_va': vs[0],
                  'va_votes': sc, 'x': ex, 'y': ey,
                  'phi_space': len(phi), 'anchor_hits': hits,
                  'va_cands': [[int(v), int(b), int(h), int(p)] for v, b, h, p in vas]}
    if not result and vas:
        # 回退: 回环候选均可读, 取 slot2 优先, 种子随候选走
        s2 = [t for t in vas if (t[0] >> 30) == 2] or list(vas)
        v0, b0, hpd0, hpt0 = s2[0]
        result = {'pid': pae.pid, 'pdpt_host': b0, 'heap_delta': d,
                  'role_id': rid, 'account': acc,
                  'entity_host': ent_host, 'entity_va': v0,
                  'va_votes': 0, 'x': ex, 'y': ey,
                  'phi_space': len(phi), 'anchor_hits': hits,
                  'va_cands': [[int(v), int(b), int(hpd), int(hpt)]
                               for v, b, hpd, hpt in vas],
                  'seeds': {'pd_host': int(hpd0), 'pt_host': int(hpt0)},
                  'note': 'fallback-slot2'}
        print(f'!! 去伪未收敛, 使用回环候选 (slot2 优先): 0x{v0:08X}')
    if result and 'seeds' not in result:
        H_pd, pdi, pt_phys, pt_host, pti = pds_ev[0]
        result['seeds'] = {'pd_host': int(H_pd), 'pt_host': int(pt_host)}
    if result:
        # 同窗口批量验证: 每个候选用自己种子读 x/y, 匹配宿主侧者胜
        def try_read(va, b, hpd, hpt):
            cache = {'pdpt': int(b),
                     'pd': {(va >> 21) & ~511: hpd},
                     'pt': {(va >> 12) & ~511: hpt}}

            def rdi32(v):
                h = pae.va2host(v, cache)
                if h is None:
                    return None
                r = pae.read(h, 4)
                return struct.unpack('<i', r)[0] if r and len(r) == 4 else None
            # 先验回环: va 页必须落回实体宿主页
            h0 = pae.va2host(va | (host_used & 0xFFF), cache)
            if h0 is None or (h0 & ~0xFFF) != (host_used & ~0xFFF):
                return None
            if host_used != ent_host:
                return (None, None, -1)   # 锚点候选: 只验回环
            full = va | (host_used & 0xFFF)   # 实体在页内的真实偏移!
            vx, vy = rdi32(full - 0x284), rdi32(full - 0x280)
            vrid = rdi32(full)
            return vx, vy, vrid

        matched = None
        pool = [t for t in vas if (t[0] >> 30) == 2] or list(vas)
        _dbg_n = [0]
        for v0, b0, hpd0, hpt0 in pool[:60]:
            r = try_read(v0, b0, hpd0, hpt0)
            if _dbg_n[0] < 3:
                _dbg_n[0] += 1
                cache = {'pdpt': int(b0),
                         'pd': {(v0 >> 21) & ~511: hpd0},
                         'pt': {(v0 >> 12) & ~511: hpt0}}
                h0d = pae.va2host(v0 | (host_used & 0xFFF), cache)
                print(f'  DBG va=0x{v0:08X} b=0x{b0:X} h0={hex(h0d) if h0d else None} '
                      f'want={hex(host_used & ~0xFFF)} r={r}')
            if r is None:
                continue
            if host_used == ent_host:
                if r[2] == rid:
                    matched = (v0, b0, hpd0, hpt0, r)
                    break
            else:
                matched = (v0, b0, hpd0, hpt0, r)
                break
        if matched:
            v0, b0, hpd0, hpt0, r = matched
            result['pdpt_host'] = int(b0)
            result['entity_va'] = int(v0 | (ent_host & 0xFFF))
            result['seeds'] = {'pd_host': int(hpd0), 'pt_host': int(hpt0)}
            if host_used == ent_host:
                result['x'], result['y'] = r[0], r[1]
                print(f'=== MATCH === entity_va=0x{v0:08X} role_id={r[2]} x={r[0]} y={r[1]}')
            else:
                result['note'] = 'space-only (anchor chain)'
                print(f'=== SPACE MATCH === base_va=0x{v0:08X} (实体页漂移, 地址空间已锁定)')
                # 确定性收尾: PD 页全量枚举 PT → 反查实体当前物理页
                import time as _t
                _H_pd, _pdi, _pt_phys, _pt_host, _pti = pds_ev[0]
                dcl = int(_pt_host) - int(_pt_phys)   # 表簇 δ
                for attempt in range(3):
                    _t.sleep(3)
                    ents3 = find_entity(pae, rid)
                    if not ents3:
                        continue
                    eh3, ex3, ey3 = ents3[0]
                    phi3 = np.uint64((eh3 & ~0xFFF) - d)
                    pfn3 = int(phi3) >> 12
                    pdpage = pae.read(H_pd, 4096)
                    if not pdpage:
                        continue
                    pda3 = np.frombuffer(pdpage, dtype='<u8')
                    found = None
                    for k in np.nonzero((pda3 & 1) == 1)[0]:
                        ep = int(pda3[k])
                        if ep >> 32 != 0 or (ep & PS_BIT):
                            continue
                        pth3 = pae.phys2host_tbl(ep & PFN_MASK)
                        if pth3 is None:
                            pth3 = int(ep & PFN_MASK) + int(dcl)
                        if not (0 < pth3 < 0x7FFFFFFEFFFF):
                            continue
                        pt3 = pae.read(pth3, 4096)
                        if not pt3:
                            continue
                        pta3 = np.frombuffer(pt3, dtype='<u8')
                        pti3 = pfn3 & 511
                        e3 = int(pta3[pti3])
                        if (e3 & 1) and (e3 >> 32) >= 0 and (e3 & PFN_MASK) >> 12 == pfn3:
                            va3 = (2 << 30) | (int(k) << 21) | (pti3 << 12) | (eh3 & 0xFFF)
                            cache3 = {'pdpt': int(result['pdpt_host']),
                                      'pd': {(va3 >> 21) & ~511: H_pd},
                                      'pt': {(va3 >> 12) & ~511: pth3}}
                            got3 = pae.va2host(va3, cache3)
                            if got3 == eh3:
                                found = (va3, pth3)
                                break
                    if found:
                        va3, pth3 = found
                        result['entity_va'] = int(va3)
                        result['entity_host'] = int(eh3)
                        result['seeds'] = {'pd_host': int(H_pd), 'pt_host': int(pth3)}
                        result['x'], result['y'] = ex3, ey3
                        result['note'] = 'entity-recovered'
                        print(f'=== ENTITY RECOVERED === va=0x{va3:08X} '
                              f'role_id={rid} x={ex3} y={ey3}')
                        break
                if result.get('note') != 'entity-recovered':
                    print('!! 实体 VA 三次重试未恢复 (漂移过快), 保留空间链')
        else:
            print('!! 同窗口批量验证全部失配 (漂移过快), 需重跑')
    if result:
        json.dump(result, open('base_map.json', 'w'), indent=1)
        json.dump(result, open('base_map.json', 'w'), indent=1)
        print(f'saved base_map.json entity_va=0x{result["entity_va"]:08X}')
    print(f'total {time.time()-t0:.0f}s')


if __name__ == '__main__':
    main()
