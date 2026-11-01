"""monitor.py — 多角色跟随坐标监听 (切账号/删角色/滑动自动跟踪).

层级化恢复:
  L1  VA 种子链读取 (快)
  L2  宿主侧直读 (已知位置)
  L3  全角色单遍扫描 (大区域, 跟随切换账号) + PD 页重定位
  L4  全量 resolve (10min 冷却)

角色名从遥测 JSON 提取. 日志打印 + 落盘 monitor.log.
"""
import json
import re
import struct
import sys
import time

import numpy as np

import sys as _sys, pathlib as _pl
_sys.path.insert(0, str(_pl.Path(__file__).resolve().parent.parent.parent))
from emu.pae import Pae, PFN_MASK, PS_BIT
from emu.pae import CHUNK, USER_VA_MAX
from emu.ldmem import read as raw_read

LOG = 'monitor.log'
BIG_MIN = 2 ** 30
FIELDS = {'x': (-0x284, '<i'), 'y': (-0x280, '<i'),
          'x2': (0x0AC, '<i'), 'y2': (0x0B0, '<i'),
          'level': (0x21C, '<i'), 'role_id': (0, '<i')}


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


class Monitor:
    def __init__(self):
        self.bm = json.load(open('base_map.json'))
        self.rid = self.bm['role_id']
        self.d = self.bm['heap_delta']
        self.hpd = self.bm['seeds']['pd_host']
        self.hpt = self.bm['seeds']['pt_host']
        self.pdi = self.bm['pdi']
        self.pti = self.bm['pti']
        self.off = self.bm['page_off']
        self.pae = Pae(pid=self.bm.get('pid') or None)
        self.names = {}
        self.last_host = self.bm['entity_host']
        self.last_resolve = 0.0
        self.consec_fail = 0

    # ---------- L1: VA 链 ----------
    def read_va(self):
        r = self.pae.read(self.hpt + 8 * self.pti, 8)
        if not r or len(r) < 8:
            return None
        e = struct.unpack('<Q', r)[0]
        if not (e & 1):
            return None
        leaf = int(e) & int(PFN_MASK)
        ent = leaf + self.d + self.off
        out = {}
        for name, (o, f) in FIELDS.items():
            rr = self.pae.read(ent + o, 4)
            if not rr or len(rr) < 4:
                return None
            out[name] = struct.unpack(f, rr)[0]
        if out['role_id'] != self.rid or not (0 < out['x'] < 3000) \
                or abs(out['x'] - out['x2']) > 2 or abs(out['y'] - out['y2']) > 2:
            return None
        return out

    # ---------- 宿主侧字段 ----------
    def _read_host_fields(self, h):
        out = {}
        for name, (o, f) in FIELDS.items():
            r = raw_read(self.pae.h, h + o, 4)[0]
            if not r or len(r) < 4:
                return None
            out[name] = struct.unpack(f, r)[0]
        return out

    # ---------- 多角色单遍扫描 (大区域) ----------
    def scan_all_roles(self, rids):
        want = np.array([int(r) for r in rids if r], dtype=np.int32)
        found = {}
        for base, size in self.pae.regions:
            if size < BIG_MIN:
                continue
            off = 0
            while off < size:
                n = min(64 * 2 ** 20, size - off)
                d, _ = raw_read(self.pae.h, base + off, n)
                if not d:
                    off += n
                    continue
                ai = np.frombuffer(d[:len(d) // 4 * 4], dtype='<i4')
                mm = np.nonzero(np.isin(ai, want))[0]
                if len(mm):
                    m2 = mm[(mm > 0xA1) & (mm < len(ai) - 0x30)]
                    if len(m2):
                        xa, ya = ai[m2 - 0xA1], ai[m2 - 0xA0]
                        xb, yb = ai[m2 + 0x2B], ai[m2 + 0x2C]
                        ok = ((xa > 0) & (xa < 3000) & (ya > 0) & (ya < 3000)
                              & (xb > 0) & (xb < 3000) & (yb > 0) & (yb < 3000)
                              & (np.abs(xa - xb) <= 2) & (np.abs(ya - yb) <= 2))
                        for i, x, y in zip(m2[ok], xa[ok], ya[ok]):
                            rid = int(ai[i])
                            if rid not in found:
                                found[rid] = (base + off + int(i) * 4, int(x), int(y))
                off += n
            break                      # 堆在大区域头部, 一块足够
        return found

    def _relocate_pt(self, eh):
        pfn = (int((eh & ~0xFFF) - self.d)) >> 12
        pti = pfn & 511
        pdpage = self.pae.read(self.hpd, 4096)
        if not pdpage:
            return None
        pda = np.frombuffer(pdpage, dtype='<u8')
        for k in np.nonzero((pda & 1) == 1)[0]:
            ep = int(pda[k])
            if ep >> 32 != 0 or (ep & 0x80):
                continue
            pth = self.pae.phys2host_tbl(ep & PFN_MASK)
            if pth is None or not (0 < pth < 0x7FFFFFFEFFFF):
                continue
            pt = self.pae.read(pth, 4096)
            if not pt:
                continue
            pta = np.frombuffer(pt, dtype='<u8')
            hit = np.nonzero(((pta & int(PFN_MASK)) >> 12) == np.uint64(pfn))[0]
            if len(hit):
                return (int(k), pth, int(hit[0]))
        return None

    # ---------- 角色名 (遥测) ----------
    def scan_names(self):
        pat = re.compile(rb'"role_id":"(\d+)"[^{}]{0,600}?"role_name":"([^"]+)"')
        pat2 = re.compile(rb'"role_name":"([^"]+)"[^{}]{0,600}?"role_id":"(\d+)"')
        for base, size in self.pae.regions:
            if size < BIG_MIN:
                continue
            off = 0
            while off < size:
                n = min(64 * 2 ** 20, size - off)
                d, _ = raw_read(self.pae.h, base + off, n)
                off += n
                if not d:
                    continue
                new = []
                for m in pat.finditer(d):
                    new.append((int(m.group(1)), m.group(2)))
                for m in pat2.finditer(d):
                    new.append((int(m.group(2)), m.group(1)))
                for rid, nm in new:
                    if rid > 100000 and rid not in self.names:
                        self.names[rid] = nm.decode('utf-8', 'replace')
                        log(f'发现角色: {rid} = {self.names[rid]}')
            break

    def name_of(self, rid):
        return self.names.get(rid) or '?'

    # ---------- L3: 跟随 ----------
    def follow(self):
        pat = re.compile(rb'"role_id":"(\d+)"')
        ids = []
        for base, size in self.pae.regions:
            if size < BIG_MIN:
                continue
            off = 0
            while off < size:
                n = min(64 * 2 ** 20, size - off)
                d, _ = raw_read(self.pae.h, base + off, n)
                off += n
                if not d:
                    continue
                for m in pat.finditer(d):
                    v = int(m.group(1))
                    if v > 100000 and v not in ids:
                        ids.append(v)
            break
        lk = json.load(open('last_known.json'))
        c = lk['anchors'].get('ANCHOR_ROLE_ID')
        rids = list(dict.fromkeys([self.rid] + ids + ([c] if c else [])))
        found = self.scan_all_roles(rids)
        if not found:
            return None
        if self.rid in found:
            rid, (h, x, y) = self.rid, found[self.rid]
        else:
            rid, (h, x, y) = next(iter(found.items()))
            old = self.rid
            self.rid = rid
            self.bm['role_id'] = rid
            log(f'切换角色: {old}({self.name_of(old)}) -> {rid}({self.name_of(rid)})')
        rec = self._relocate_pt(h)
        if rec:
            pdi, pth, pti = rec
            self.bm['pdi'], self.bm['pti'] = pdi, pti
            self.bm['seeds']['pt_host'] = pth
            self.bm['entity_host'] = h
            json.dump(self.bm, open('base_map.json', 'w'), indent=1)
            self.hpt, self.pdi, self.pti = pth, pdi, pti
            log(f'VA 链重接: pdi={pdi} pti={pti} host=0x{h:X}')
        else:
            log('页链未接上 (PD 页漂移?), 暂用宿主侧读数')
        self.last_host = h
        r = self._read_host_fields(h)
        return (rid, r, h)


def main():
    m = Monitor()
    log(f'监听启动 role={m.rid}({m.name_of(m.rid)}) pid={m.pae.pid}')
    t_name = time.time()
    while True:
        ts = time.strftime('%H:%M:%S')
        r = None
        try:
            r = m.read_va()
        except Exception:
            r = None
        if r:
            m.consec_fail = 0
            nm = m.name_of(m.rid)
            line = f"{m.rid}({nm}) ({r['x']},{r['y']}) lv={r['level']}"
            print(f"\r[{ts}] {line}   ", end='', flush=True)
            with open(LOG, 'a', encoding='utf-8') as f:
                f.write(f"[{ts}] {line}\n")
        else:
            m.consec_fail += 1
            r = None
            try:
                rr = m._read_host_fields(m.last_host)
                if rr and rr['role_id'] == m.rid and 0 < rr['x'] < 3000:
                    r = rr
            except Exception:
                pass
            if r:
                nm = m.name_of(m.rid)
                line = f"{m.rid}({nm}) ({r['x']},{r['y']}) lv={r['level']} [宿主侧]"
                print(f"\r[{ts}] {line}   ", end='', flush=True)
                with open(LOG, 'a', encoding='utf-8') as f:
                    f.write(f"[{ts}] {line}\n")
            elif m.consec_fail % 10 == 0:
                print(f"\r[{ts}] 失联 x{m.consec_fail}, 扫描角色 ...   ",
                      end='', flush=True)
            if m.consec_fail >= 8:
                try:
                    res = m.follow()
                    if res:
                        m.consec_fail = 0
                except Exception as e:
                    log(f'L3 异常: {e}')
            if m.consec_fail >= 300 and time.time() - m.last_resolve > 600:
                log('L4 全量 resolve ...')
                m.last_resolve = time.time()
                try:
                    import resolve
                    resolve.main()
                    m.__init__()
                    m.consec_fail = 0
                    log('L4 完成')
                except Exception as e:
                    log(f'L4 异常: {e}')
        if time.time() - t_name > 90:
            t_name = time.time()
            try:
                m.scan_names()
            except Exception:
                pass
        time.sleep(2)


if __name__ == '__main__':
    main()
