# Remi — 抓取管道

湾区超市优惠的抓取、校验、入库管道。这个仓库只包含**数据管道和管理后台**，不包含产品本身（agent、路线优化、购物清单）。

---

## 当前状态（2026-08-28）

**99 Ranch 抓错了店，抓了四个月，还没修。** 其余三家正常。

| 店 | 数据来源 | 状态 |
|---|---|---|
| Safeway | Flipp 开放接口 | 正常 |
| Cardenas | Flipp 开放接口 | 正常 |
| H Mart | Flipp 开放接口 | 正常 |
| 99 Ranch | 自家 JSON 接口 | ⚠️ **数据源错误，抓的是南加州仓库；且该板块已空，每天抓取失败** |

`download_99ranch_flyer.py` 里 `STORE_ID = 8899`，注释写着 `# Mountain View`——但 8899 是
Walong Marketing 的仓库（Buena Park，南加州），Mountain View 是 **1772**。抽 5 件商品实测，
**5/5 价格都和湾区门店不同**，4/5 我们报的「特价」比湾区日常价还贵。

**每天早上 6:00 自动跑**（`com.remi.pipeline`）。99 Ranch 那步会失败
（`themeId=18: API returned 0 products`——那个仓库板块空了）。

### 下次从这里开始

三份文档，分工不重叠：

| 文档 | 回答 |
|---|---|
| `../docs/260807_Ingestion_Design_v3.md` | **怎么抓** —— 正确端点、覆盖范围、每日对账、已排除的五条路（别再查一遍）|
| `../docs/260808_FieldAudit_v2.md` | **源上有什么** —— Flipp 56 / Ranch 商品库 51 字段，逐个留决定 |
| `../docs/260828_FieldSet_v1.md` | **要抄哪些格子** —— 读传单的 21 个字段、怎么抄、已知硬伤 |
| `../docs/260828_FieldsAudit_from_Requirements_v1.md` | **我们要回答什么** —— 七棵需求树 + 倒着验出来的缺口清单 |

正确的数据源是**门店传单**，只有图，没有任何文字接口——只能用视觉模型读。
已验证：11 页 231 条全跑通，`unmapped` **实测 4 条**
（3 条是原价旁重复印的附加费 —— 已改 `surcharges` 规则吸收，**但那次改动没跑过验证**；
1 条是 `NEW ITEM` 角标，故意留着观察）。

顺序（不能换，第 1 步必须在第 2 步前）：

```
0.  ✅ 视觉模型 —— Gemini key 已填 .env；Sonnet 28/30 胜 Gemini Flash 22/30，最终选型未定
1.  🟡 定一套所有店共用的字段
       ✅ 99 Ranch 传单读图这半边已收口 → 260828_FieldSet_v1.md
       ⬜ 传单 → 商品库单品匹配（56% 基线，要定名字+净重双键规则）
       ⬜ 跟 Flipp 56 字段对应、取并集
       ⬜ 拿需求树倒着验一遍（找缺口，不是筛字段）→ FieldsAudit_from_Requirements
2.  ⬜ 99 Ranch 改成读传单
3.  ⬜ Flipp 三家对齐到同一套字段
4.  ⬜ 管理后台（已重建，但字段未统一）
5.  ⬜ normalization
6.  ⬜ category
```

**没落实的两条**：存 viewAd 的 `imageUrl` + ETag（旧图 URL 实测过期两周仍可访问，
所以不用存图；哪天 404，每日对账会当天报）；轮换 6 个 API 密钥。

### 待办（更详细的列表见 `../docs/`）

- **normalization —— 单位解析约有九分之一是错的**（原来排在「下一步」，8-07 让位给 99 Ranch 数据源，内容仍然成立）

  ```
  绞牛肉   传单写 $4.99/磅，"6 lbs. or more"  →  系统算成 $0.83/磅（把起购量当成了包装规格）
  金枪鱼   "5.29 OZ X 4 EA/PKG"              →  系统算成 $21.14/磅（只看单罐重量）
  ```

  四类已知错误：起购量当规格、数量×单件、多商品同格、区间取低端。
  根因在 `import_to_database.py`：`package_amount` 每个分支都写死 1，规格未知时按「1 个单位」除，所以永远返回一个数、每层检查都能通过。
  **不需要重新抓取**——原始描述文字已在 `deal_conditions` 字段里，改好解析逻辑拿现有数据重算即可。（仅限 Flipp 三家；99 Ranch 那 174 条来源本身无效，重算没有意义。）
- promotion 类型完整梳理（Mix & Match、BOGO、买 N 件、会员价、限购、券）—— 本周 464 条里有 43 条属于此类，价格记的是组合价，单买会更贵
- 商品分类 —— Flipp 不提供，目前三家店的 `category` 全空
- 价格历史 / 省钱基准 —— Flipp 只给约 1% 的原价（商家整期都是会员价），这个缺口还没有数据源
- Flipp 依赖风险 —— 三家店走同一个非官方接口，上线前需要方案
- **轮换 6 个 API 密钥** —— 2026-03 提交进 git 历史，永久可见

---

## 这个网站是什么

**`http://localhost:8080/` 是管理后台，不是给用户看的页面。** 产品最终形态是 AI agent app；这个网站的作用是让你看到抓取管道的状态。

页面分两块：

**上半：健康状态条。** 正常时安静，出事时变红并列出每一条问题。展开是每家店一行、7 列：当前数据 / 最后成功 / 连续成功次数 / 近 10 次 / 累计 / 近况（成败序列）/ 上次运行。

**下半：逐条检查表**（2026-08-06 重建）。本周已上线的每一条优惠一行：

- **第一行** —— 解析后的字段，一屏看到最多的列
- 展开后 **第二行** 整行铺开：这条数据的原始来源文字
- 展开后 **第三行** 整行铺开：解析过程和依据
- **故障码**是可增删的标签（Notion 那种），检测器自动打的可以标「误报」或「已解决」并留原因，检测器没看出来的可以手动加

故障码由 `console_faults.py` 自动算，**全部是只读判断，不改数据**，且不依赖任何外部价格基准——每一项要么是机械的（字段为空、数字被忽略），要么是内部矛盾的（传单自己印了单价，和我们算的对不上）。手动增删存在 `deal_fault_marks` 表（`console_marks.py`），跟着 `deals.id` 走，重算不丢、换周随批次退休。

---

## 运行

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 只需要 FLIPP_POSTAL_CODE
```

抓取不需要任何密钥。`.env` 里的 Vertex 配置只有 `app.py` 的聊天原型在用。

```bash
python3 run_pipeline.py              # 全部四家店
python3 run_pipeline.py safeway      # 单独一家
python3 pipeline_status.py           # 命令行看状态
python3 app.py                       # 管理后台 → localhost:8080
```

### 定时任务

```bash
./scripts/install_launchd_job.sh              # 默认每天 6:00
./scripts/install_launchd_job.sh --hour 7     # 改时间
./scripts/uninstall_launchd_job.sh
```

日志在 `logs/pipeline.out.log` 和 `logs/pipeline.err.log`。

---

## 加一家新店

**改一个地方**：`stores.py`

```python
"costco": {
    "display_name": "Costco",
    "source": "flipp",
    "flipp_merchant": "Costco",
    "enabled": True,
},
```

导入目录、流水线步骤、网站列表都是从这里推导的，不用另外声明。

Flipp 覆盖的湾区杂货店包括 Costco、Sprouts、Lucky、FoodMaxx、Grocery Outlet、Smart & Final、Save Mart、Mitsuwa 等。**Whole Foods 和 Trader Joe's 不在 Flipp 上**，需要另写抓取器（`source` 换成新类型，并在 `PIPELINE_STEPS` 里加对应步骤）。

---

## 文件

| 文件 | 作用 |
|---|---|
| `stores.py` | **唯一声明店铺的地方** |
| `download_flipp_flyer.py` | 抓取所有 Flipp 商家 |
| `download_99ranch_flyer.py` | 99 Ranch 自己的接口 —— **数据源错误，待改为读门店传单图**，见 `../docs/260807_Ingestion_Design_v3.md` 和 `../docs/260828_FieldSet_v1.md` |
| `parse_99ranch_deals.py` | 99 Ranch 的解析（Flipp 的抓取器直接产出成品，不需要单独解析）|
| `deal_fields.py` | 各解析器共用的字段规范化 |
| `import_to_database.py` | 入库 + 调用校验 |
| `publish_gate.py` | **唯一决定数据能否上线的地方** |
| `pipeline_status.py` | 健康状态计算 |
| `database_utils.py` | 批次模型 |
| `migrate_batch_independence.py` | 数据库结构迁移（可重复运行）|
| `run_pipeline.py` | 调度器 |
| `app.py` | 管理后台 |
| `console_faults.py` | 故障码检测（只读，无外部基准）|
| `console_marks.py` | 故障码的人工增删，存 `deal_fault_marks` 表 |
| `static/inspect.js` | 检查表前端渲染（数据走 JSON，服务端不渲染行）|
| `parse_taxonomy.py` | 分类解析 |
| `setup_database.py` | 建库 |

---

## 关键机制

### 批次独立

每个批次拥有自己的数据行，唯一键是 `(batch_id, product_key)`。

**为什么重要**：以前的唯一键是 `(商品名, 店名, 生效日)`，重新导入同一周会把已上线批次的数据行**划走**。实测 99 Ranch 上线批次从 181 条变成 0 条，而状态还写着"已发布、181 条"。任何跑在导入之后的检查都是在检查一家已经空了的店。

### 发布校验

只有两种情况硬拒绝：**一条数据都没有**，或**数据源变形了**（单位全空 / 有价格的不到 20%）。

其余全部放行但警告：条数骤降、有价比例低、单位比例低、已过期、尚未生效。

**为什么不是全部拒绝**：拒绝意味着那家店空着，直到有人回来看见。宁可放进一批可疑数据并大声警告，也不要在无人值守期间让店空掉——那正是要修的故障本身。

### 过期下架

超过 14 天没更新的已上线批次会被自动下架。**"已上线"= agent 会拿去用的数据**，所以留着过期价格是数据问题，不是显示问题。

### 每天跑 + 跳过

跳过判断问数据库（"有没有通过校验并上线的当周数据"），不问文件系统。

**为什么**：文件存在只证明写过东西，批次上线才证明通过了校验。以前"文件存在就跳过"让一次抓取失败变成永久——写出空文件后，之后每次运行（包括手动重跑）都跳过它。99 Ranch 有五周是这么丢的。

三个额外条件：有更新的失败批次 → 不跳；超过 8 天没真抓 → 不跳；同一天最多真抓一次。

---

## 已知不足

- **单位解析约 9% 有误**（见上方「待办」第一条）
- **分类全空**（Flipp 不提供）
- **原价覆盖约 1%**（商家整期只标会员价）
- **选错来源无法察觉 —— 这条已经兑现了。** 99 Ranch 用错门店 ID 四个月，`publish_gate.py` 的 4 项硬拒 + 7 项告警**一项都没响**。因为它们全都在问「数据长得对不对」（有没有条数、有没有价格、有没有单位、有没有过期、条数有没有骤降），没有一项在问「**这是不是那家店**」。
  最接近的一项是「混入了别家店的数据」，但它比的是批次里的 `store_name` 和批次自己声明的店名——两边都是我们自己贴的标签，标签贴错时两边一致，检查照样通过。
  **目前唯一能发现这类错误的办法，是人拿真实门店的价格去比对。** 两份传单商品数接近时同理。
- `parse_compound_deals.py` 目前无人调用，留着给下一阶段处理多件促销用

---

## 协作约定

- 代码和文档进 git；生成的传单文件、日志、数据库、`.env` 不进
- 规划文档在 `../docs/`（仓库之外，按 `YYMMDD_主题_版本` 命名）
- README 和旧笔记冲突时，以代码和本文件为准
