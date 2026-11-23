# DanClub Terminal

> **100% 开源、免费、免鉴权的多市场实时情报终端 & `CORS *` 只读金融 JSON API。**
>
> Live 实时主站（3 分钟同步）：https://danclub.duckdns.org  
> GitHub Pages 开源镜像（15 分钟同步）：https://danclub-open.github.io/danclub-terminal/  
> 社区交流群（抱团取暖打气群）：https://qm.qq.com/q/ZHFHZujCak  
> 开源协议：MIT License（零付费墙、零返佣引流、零私密数据）

---

## 一、为什么不一样（核心差异化优势）

1. **⚔️ 实盘巨鲸母舰空战战情室（首屏直达）**
   - 浏览器直连公开成交 WebSocket，绿军多头 vs 红军空头 60fps 战术空战，按成交体量分层出击（光束 `<$5万` / F-22 轻战机 `≥$5万` / Su-57 重型截击机 `≥$25万` / B-2 隐形轰炸机 `≥$100万`），带物理级前线推拉冲撞、五国交响战歌高潮轮播与副屏全屏模式；
   - **真金白银巨鲸压阵**：基于 2,000+ 实盘建档、实体去重后的巨鲸矩阵，区分 **S/A 级聪明钱（金环）** 与 **C 级反指巨鲸（紫环）**，支持展示公开 **X (Twitter) 账号直达**、点击母舰拉出全仓多币种 **开仓均价 / 强平价 / 杠杆 / 实时浮盈亏** 与 15m 净敞口轨迹。
2. **24/7 美股 bStocks 实时映射 + 7 项底部共振总览**
   - 美股休市/盘前盘后期间自动以 **币安现货 bStocks（`*BUSDT`）** 为主报价，同时保留官方上一常规盘昨收对照，彻底告别周末与盘后死价；
   - 7 项周期底部共振矩阵、恐慌贪婪、矿机关机价、US10Y 收益率、场内美股 QDII 高溢价排雷榜。
3. **跨市场硬核套利与资金流雷达**
   - 中证股指期货 `IC/IM/IF/IH`（含 2706 远月）年化贴水安全垫期限曲线；
   - 海力士美股 ADR×10 / 韩股、黄金 `XAU/XAUT/PAXG` 72h 价差 Z-Score 偏离带；
   - BTC/ETH 现货 ETF 流向、MSTR mNAV 折溢价、Hyperliquid 持仓、币安 OI + 极端资金费率防轧空双雷达、四所充提防飞刀监控。
4. **多源分类快讯聚合（融合方程式新闻 EQN + 全球快讯）**
   - 清晰划分 **加密快讯 / 美股·宏观 / 上新·公告 / 异动·OI / 深度预警**，兼顾时效与广度。

---

## 二、开放只读 JSON API 快速接入（免 Key · 支持 `CORS *`）

所有数据均以静态 JSON 托管，**无需注册、无需 API Key、浏览器前端/后端脚本/AI Agent 均可直接调用**：

| 节点 | Base URL | 同步频率 | 适用场景 |
| --- | --- | --- | --- |
| **高速主节点（推荐）** | `https://danclub.duckdns.org/api/v1` | 每 3 分钟 | 实盘看板、Bot 轮询、国内直连 |
| **GitHub Pages 镜像** | `https://danclub-open.github.io/danclub-terminal/api/v1` | 每 15 分钟 | 全球 CDN 兜底、静态镜像 |

### 7 个公开端点一览

| 端点 | 内容说明 | 核心字段 |
| --- | --- | --- |
| `GET /index.json` | 索引目录、最新快照时间戳与各接口元信息 | `meta`, `endpoints` |
| `GET /whales.json` | **实体去重巨鲸阵营对峙仪 + Top 40 在役实盘旗舰** | `cohorts` (`SMART_SA` vs `COUNTER_C`), `divergence`, `series`, `flagships[]`（含 `x_handle`, `positions[]` 开仓/强平/杠杆/浮盈亏） |
| `GET /overview.json` | 市场态势、5 秒交易决策信号、7 项底部共振、宏观/bStocks、QDII 溢价 | `market_regime`, `desk_signals`, `bottom_dashboard`, `macro_quotes`, `qdii_premium` |
| `GET /arb.json` | 跨市场价差（海力士/黄金）、中证股指贴水期限结构、四所充提安全 | `spreads`, `csi_basis`, `wallet_status` |
| `GET /flows.json` | 美国现货 ETF 流向、MSTR mNAV、Hyperliquid 持仓、币安 OI/极值费率 | `etf_flows`, `mstr`, `hyperliquid`, `bn_oi_radar` |
| `GET /labs.json` | 3 套公开模拟定投策略（`FIXED`/`VA`/`MAFG`）与在期交易所活动 | `dca_simulation`, `exchange_promos` |
| `GET /feed.json` | 多源融合分类市场快讯（融合 EQN 方程式新闻 + 全球头条与交易所公告） | `items[]`（含 `cat_code`, `category`, `source`, `ts`, `coins`, `url`） |

> 完整字段 Schema 与返回样例请阅读：[docs/API.md](./docs/API.md)。

### 10 秒接入范例

#### 1. `curl` 命令行
```bash
# 获取聪明钱 vs 反指巨鲸实时背离与前 5 大实盘旗舰
curl -s https://danclub.duckdns.org/api/v1/whales.json | jq '{
  btc_divergence: .cohorts.BTC.divergence,
  top5: [.flagships[:5][] | {display, x_handle, tier: .tier_code, primary: .primary_coin, side: .primary_side, usd: .total_notional_usd}]
}'

# 获取 24/7 美股 bStocks 与市场高优先级信号
curl -s https://danclub.duckdns.org/api/v1/overview.json | jq '{regime: .market_regime, signals: .desk_signals}'
```

#### 2. Python（零外部依赖，完整脚本见 [`examples/python_whale_signal.py`](./examples/python_whale_signal.py)）
```python
import json, urllib.request

BASE = "https://danclub.duckdns.org/api/v1"
whales = json.loads(urllib.request.urlopen(f"{BASE}/whales.json", timeout=10).read())
div = whales["cohorts"]["BTC"]["divergence"]
print(f"BTC 巨鲸背离度: {div['spread_pct']:+.1f}% ({div['label']})")
for f in whales["flagships"][:5]:
    x_tag = f" (@{f['x_handle']})" if f.get("x_handle") else ""
    print(f"- {f['display']}{x_tag} [{f['tier_code']}] {f['primary_coin']} {f['primary_side']} ${f['total_notional_usd']/1e6:.2f}M")
```

#### 3. JavaScript / Node.js / 浏览器前端（完整脚本见 [`examples/node_live_monitor.mjs`](./examples/node_live_monitor.mjs)）
```js
const res = await fetch("https://danclub.duckdns.org/api/v1/whales.json");
const data = await res.json();
console.log("Smart vs Counter (BTC):", data.cohorts.BTC.divergence);
```

#### 4. 调用礼仪
- 主站静态快照每 3 分钟刷新一次，建议客户端轮询间隔 **≥ 60 秒**（响应自带 `Cache-Control: public, max-age=30, stale-while-revalidate=60`）；
- 本接口完全免费开放，引用或二次开发欢迎保留项目出处链接。

---

## 三、三层物理隔离安全架构

```text
[公开数据源 SQLite (mode=ro) + 公开市场接口]
       │ 只读白名单导出 (exporter/public_terminal_exporter.py + scripts/whale_public_exporter.py)
       ▼
[本地 Fail-Closed 隐私防火墙 (scripts/privacy_guard.py)]
  ├── 递归扫描所有键名与字符串（拦截邮箱/手机号/私钥/Token/内网IP/私有黑名单）
  ├── 剥离所有推广返佣参数、非 https:// 链接与内部备注标签
  └── 命中任意违规项即熔断中止发布（exit code 2）
       │ 内容哈希防缓存 + SHA-256 白名单校验 (scripts/sync_public_data.py)
       ▼
[只读静态边缘节点 + GitHub Pages] ──▶ 全球免鉴权访问（源机零暴露）
```

---

## 四、本地运行与开发测试

```bash
# 运行完整回归单元测试
python3 -m unittest discover -s tests -v

# 运行开放 API 接入示例
python3 examples/python_whale_signal.py
node examples/node_live_monitor.mjs

# 本地预览前端
python3 -m http.server 8080 --directory site
```

---

## 五、开源与公版音频鸣谢

- 战情室内置 5 首多国交响战歌高潮片段均取材自 Wikimedia Commons 公开授权录音：
  - 柴可夫斯基《1812 序曲》与罗西尼《威廉·退尔序曲》— U.S. Marine Band（Public Domain）
  - 比才《卡门 · 斗牛士之歌》— U.S. Air Force Band & Singing Sergeants（Public Domain）
  - 瓦格纳《女武神的骑行》— Philip Milman（CC BY 3.0）
  - 霍尔斯特《火星：战争使者》— Skidmore College Orchestra / Musopen（Attribution）
- 公开市场快讯频道融合支持 [方程式新闻 Equation News (eqn.news)](https://eqn.news/) 与多源全球公开行情接口。

## 免责声明

本项目所有数据与接口均为公开市场信息聚合与开源量化研究工具，**不构成任何投资、法律或税务建议**，不提供代客理财、付费喊单或返佣引流。市场有风险，决策请独立判断。
