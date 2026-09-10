---
name: radio-sdr
description: Use for RF/SDR security research including signal identification, replay feasibility study in shielded labs, and wireless protocol analysis outside classic Wi-Fi.
---

# RF / SDR Security Research

## ACTION REQUIRED（读完后立刻执行）

1. `NOW`: 发射实验默认在屏蔽室/实验目标进行（默认只收）
2. `NOW`: scope 写明设备、频段（默认只收）
3. `ACT`: 只接收识别 → 解调分析 → 实验室复现评估

## 适用场景

- 无线遥控/传感器等非 Wi-Fi RF
- ADS-B/遥控等协议研究（接收）
- 与 wifi-wireless 分工：本 skill 偏 **SDR 通用 RF**；Wi-Fi 攻防走 R29

## 工作流

```text
□ 频段与设备确认
□ 只收：识别中心频率与调制
□ GNU Radio / URH 分析
□ 重放仅在屏蔽室
□ 结论侧重：是否可未授权控制 / 加固建议
```

## 工具链

| 工具 | 用途 |
|------|------|
| RTL-SDR / HackRF | 收发硬件 |
| URH / GNU Radio | 分析 |
| Inspectrum | 信号 |

## 参考

- `references/sdr-lab-rules.md`
- `../wifi-wireless/` `../ot-ics/` `../hardware-security/`

## 路由上下文

**上游**: MASTER R38  
**注意**: 发射实验使用屏蔽室/假负载

## 任务完成自检

- [ ] 是否默认只收并记录频段？
- [ ] Checklist？