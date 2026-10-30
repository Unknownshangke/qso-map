# QSO 点阵世界地图（QRZ 主页顶部控件）

一个「网格世界地图 + 通联点亮」的小控件：

- 世界地图用暗色点阵做轮廓（120 × 56 格，等距圆柱投影，已裁掉南极）；
- 每个通联过的网格点亮成一个实心圆点；
- **离现在越近，圆点越大、越亮**（默认回看 24 个月）；
- 输出 `qso-map.svg`（矢量，透明底）和 `qso-map.png`（2 倍图），直接 `<img>` 嵌进 QRZ 主页。

> QRZ 的 Bio 页面会过滤 `<script>` 和 `<style>`，所以这类控件不能在前端实时绘制。
> 这里的做法是：**在别处（本机 / GitHub Actions）算好，渲染成图片，再让 QRZ 引用图片地址**。
> 图片地址不变、内容每天刷新，看起来就像"活的"。

## 目录

```
generate.py                   生成脚本（取数 → 解析 → 投影 → 出图）
geo_prepare.py                一次性：从 world-atlas 生成地理数据
geo/land_mask.json            世界陆地掩膜（120×56）
geo/country_centroids.json    国家中心点（无网格时的兜底定位）
geo/land-110m.json            原始 TopoJSON（只有 geo_prepare 用）
.github/workflows/update.yml  每天自动跑一次并提交新图（GitHub Actions）
qso-map-demo.svg / .png       合成数据的示例输出（可以直接打开看效果）
```

## 1. 先看效果（不需要任何数据）

```bash
python generate.py --demo --png
```

## 2. 数据源（可单用，也可叠加）

| 来源 | 参数 | 说明 |
|---|---|---|
| 演示数据 | `--demo` | 340 条合成记录，只看效果 |
| ADIF 文件 | `--adif 文件` | QRZ / LoTW / N1MM 等导出的 ADIF，可重复给多个 |
| QRZ Logbook API | `--key` 或 `QRZ_API_KEY` | 需要 **QRZ 订阅** |
| LoTW 查询接口 | `--lotw-user` / `--lotw-pass` | LoTW 网站账号，不是 TQSL 证书密码 |

### 方式 A：ADIF 导出（免费，最稳）

- QRZ Logbook 网页 → **Download log** → ADIF；
- LoTW 网页 → **Your QSOs → Download Report**（或 TQSL）→ ADIF。

两个文件可以合并，重复的 QSO 会自动去重：

```bash
python generate.py --adif BA4RKS-qrz.adi --adif BA4RKS-lotw.adi --png
```

### 方式 B：QRZ Logbook API（需订阅）

```bash
set QRZ_API_KEY=xxxx-xxxx-xxxx-xxxx        # Windows
export QRZ_API_KEY=xxxx-xxxx-xxxx-xxxx     # macOS / Linux
python generate.py --key %QRZ_API_KEY% --png
```

`ACTION=FETCH&OPTION=BETWEEN:起始+结束,MAX:5000`，User-Agent 按 QRZ 要求写成 `BA4RKS-qso-map/1.0 (BA4RKS)`。

### 方式 C：LoTW 直连（ARRL 官方查询服务）

LoTW 提供一个公开的 REST 查询端点
[`lotwreport.adi`](https://lotw.arrl.org/lotw-help_devel_de/developer-query-qsos-qsls)，
登录名和密码走 URL 参数，返回 ADIF：

```
https://lotw.arrl.org/lotwuser/lotwreport.adi
  ?login=<LoTW用户名>&password=<LoTW密码>&qso_query=1
  &qso_qsl=no&qso_qsldetail=yes&qso_withown=yes
  &qso_startdate=2024-11-01&qso_enddate=2026-10-30
```

脚本里已经封装好：

```bash
set LOTW_USER=BA4RKS
set LOTW_PASS=你的LoTW密码
python generate.py --lotw-user %LOTW_USER% --lotw-pass %LOTW_PASS% --png
```

和 QRZ 同时使用（去重后合并）：

```bash
python generate.py --key %QRZ_API_KEY% --lotw-user %LOTW_USER% --lotw-pass %LOTW_PASS% --png
```

**LoTW 的一个限制**：这个接口只在 QSO **已确认**（`QSL_RCVD=Y`）且带 `qso_qsldetail=yes` 时才返回
`DXCC / COUNTRY / GRIDSQUARE`。未确认的 QSO 只有呼号、时间、波段，没有位置，画不到地图上——
脚本会跳过它们并打印数量。所以 LoTW 这一路实际贡献的是"已确认通联"。

> 凭证安全：LoTW 密码等同于你的 LoTW 账号，只放环境变量 / GitHub Secrets，
> 不要写进脚本、不要提交到仓库。

## 3. 自动更新（推荐）

把本目录作为仓库根目录推到 GitHub，开启 **Pages**（main / 根目录），
在 **Settings → Secrets and variables → Actions** 添加：

- `QRZ_API_KEY`（可选）
- `LOTW_USER` + `LOTW_PASS`（可选）

两个都不配也能跑，但会报错退出。`.github/workflows/update.yml` 每天 03:17 UTC
重新生成并提交 `qso-map.svg` / `qso-map.png`，Pages 自动发布：

```
https://<用户名>.github.io/<仓库名>/qso-map.svg
```

## 4. 嵌进 QRZ 主页

```html
<img src="https://<用户名>.github.io/<仓库名>/qso-map.svg" alt="" style="width:100%;height:auto;display:block;border:0;">
```

图片是透明底，放在深色头图里会直接融合。

## 5. 可调参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--months 24` | 24 | 回看月数；超出的记录不画，尺寸按这个窗口归一化 |
| `--name qso-map` | `qso-map` | 输出文件名 |
| `--out .` | `.` | 输出目录 |
| `--png` | 关 | 额外用本机 Chrome/Edge 渲染 2 倍 PNG |

配色和点径在 `generate.py` 顶部常量里：

```python
PITCH = 9.0               # 网格间距（SVG 单位）
LAND_R = 1.2              # 陆地小点半径
R_NEW, R_OLD = 4.6, 2.2   # 最新 / 最旧通联点半径
LAND_FILL = "#3c5877"     # 陆地颜色（深色背景上用）
DOT_FILL = "#2dd4bf"      # 通联点颜色
```

## 6. 定位精度

优先级：`GRIDSQUARE` → `LAT/LON` → `COUNTRY`（国家中心点）。

- FT8/FT4 记录基本都带网格，位置准；
- SSB/CW 没有网格时退到国家中心点，落在该国中部，属于"大概位置"；
- 两者都没有的记录会被跳过，脚本会打印统计（mapped / skipped）。

## 7. 隐私

公开的只是一张聚合图：没有对方呼号、时间、频率，只有"哪些网格通过联"。
如果连这个也不想公开，把 `--months` 调小，或者不部署这个控件。
