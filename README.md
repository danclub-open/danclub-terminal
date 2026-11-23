# DanClub Open Terminal

> **100% 开源、免费、零个人隐私泄露的多市场金融情报终端与开放只读 JSON API。**

- **在线站点（GitHub Pages 免费 CDN）**：https://danclub-open.github.io/danclub-terminal/
- **开源协议**：MIT License
- **核心理念**：除了作者个人隐私外，全部前端界面、只读数据导出器、Fail-Closed 隐私防火墙脚本、系统架构与聚合后的公开市场数据接口，均无偿向社区开放。

---

## 核心功能模块

1. **总览与 7 项底部共振仪表盘**
   - 追踪 BTC 守住前低、现货 ETF 流向、合约杠杆与费率、稳定币供给、美债 10Y、Coinbase 溢价、币股联动情绪；
   - 集成恐慌贪婪指数、全网稳定币变动、BTC 主流矿机关机价（S21 Pro / S21 / S19k）、跨市场宏观映射与 A 股场内 QDII 溢价风险榜。
2. **跨市场价差与套利实验室**
   - **海力士美股 ADR×10 vs 韩股合约**、**现货黄金 XAU vs 链上黄金 XAUT / PAXG**：采用动态自适应纵轴 + 72h 常态带（±1σ Z-Score）+ 近 14 天日内波动带；
   - **中证股指期货贴水期限结构**：覆盖 `IF / IC / IM / IH` 全活跃合约及远月 `2706` 贴水年化；
   - **四所充提安全雷达**：追踪 Binance / OKX / Bitget / Gate 充提暂停状态与主流币异常警示（防接停充提飞刀）。
3. **资金流、机构动向与杠杆雷达**
   - 美国 BTC / ETH 现货 ETF 近 15 交易日分发行商流向矩阵；
   - Strategy (MSTR) 持仓、SEC 8-K 披露与 mNAV 7 日曲线；
   - Hyperliquid 全网 OI、援助基金（Assistance Fund）HYPE 持仓与主流合约年化费率；
   - 币安 U 本位 1h 杠杆急增雷达（严格按合约币数 `OI Qty` 口径，附带基于历史实盘回测的追多拥挤/防逼空风控标签）。
4. **公开模拟实验室与稳健活动清单**
   - 3 套纯虚拟资金模拟定投对比盘（固定定投 vs 价值平均 vs 200日均线+恐慌加权）长期公开透明对照；
   - 交易所公开活动清单（自动剥离任何推广/邀请/返佣参数）。
5. **脱敏高信噪比快讯与免鉴权免费 JSON API**
   - 免费开放只读静态接口 `/api/v1/{index,overview,arb,flows,labs,feed}.json`，支持 CORS，可直接被脚本、Bot、Excel 或 Notion 调用。

---

## 安全与零隐私架构（三层物理隔离）

```text
[公开数据源 SQLite (mode=ro)]
       │ 只读白名单导出 (exporter/public_terminal_exporter.py)
       ▼
[本地 Fail-Closed 隐私防火墙 (scripts/privacy_guard.py)]
  ├── 递归扫描所有键名与字符串（拦截邮箱/手机号/私钥/Token/内网IP/私密词）
  ├── 剥离所有推广返佣参数与非 https:// 链接
  └── 命中任意违规项即熔断中止发布（exit code 2）
       │ 单向孤儿提交推送 (scripts/sync_public_data.py)
       ▼
[GitHub Pages 静态 CDN (gh-pages 分支)] ──▶ 全球免费只读访问（源机零暴露）
```

---

## 本地运行与测试

```bash
# 运行隐私防火墙与导出器全套单元测试
python3 -m unittest discover -s tests -v

# 本地预览前端（含样例或最新静态 API）
python3 -m http.server 8080 --directory site
```

---

## 免责声明

本项目所有内容均为公开市场数据聚合与开源量化研究工具，**不构成任何投资、法律或税务建议**，不提供代客理财、付费喊单或返佣引流。市场波动剧烈，请独立研究并审慎决策。
