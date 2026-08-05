# Remi — 抓取管道

湾区超市优惠的抓取、校验、入库管道。这个仓库只包含**数据管道和管理后台**，不包含产品本身（agent、路线优化、购物清单）。

---

## 当前状态（2026-08-04）

**四家店都在正常抓取，数据都在有效期内。**

| 店 | 数据来源 | 本周 |
|---|---|---|
| Safeway | Flipp 开放接口 | 194 条 |
| Cardenas | Flipp 开放接口 | 221 条 |
| H Mart | Flipp 开放接口 | 46 条 |
| 99 Ranch | 自家 JSON 接口 | 174 条 |

**每天早上 6:00 自动跑**（`com.remi.pipeline`）。已有当周数据的店会跳过，只花约 1 秒、0 次网络请求。

### 下次从这里开始

**第一阶段已完成**：数据能进来、坏数据挡得住、出事看得见、每天自动跑。

**下一步是 normalization** —— 单位解析目前约有九分之一是错的：

```
绞牛肉   传单写 $4.99/磅，"6 lbs. or more"  →  系统算成 $0.83/磅（把起购量当成了包装规格）
金枪鱼   "5.29 OZ X 4 EA/PKG"              →  系统算成 $21.14/磅（只看单罐重量）
```

四类已知错误：起购量当规格、数量×单件、多商品同格、区间取低端。

**不需要重新抓取**——原始描述文字已经存在数据库的 `deal_conditions` 字段里，改好解析逻辑后拿现有数据重算即可。

### 待办（更详细的列表见 `../docs/`）

- promotion 类型完整梳理（Mix & Match、BOGO、买 N 件、会员价、限购、券）—— 本周 464 条里有 43 条属于此类，价格记的是组合价，单买会更贵
- 商品分类 —— Flipp 不提供，目前三家店的 `category` 全空
- 价格历史 / 省钱基准 —— Flipp 只给约 1% 的原价（商家整期都是会员价），这个缺口还没有数据源
- Flipp 依赖风险 —— 三家店走同一个非官方接口，上线前需要方案
- **轮换 6 个 API 密钥** —— 2026-03 提交进 git 历史，永久可见

---

## 这个网站是什么

**`http://localhost:8080/` 是管理后台，不是给用户看的页面。** 产品最终形态是 AI agent app；这个网站的作用是让你看到抓取管道的状态。

首页顶部是健康状态条：正常时安静，出事时变红并列出每一条问题。展开可以看到每家店的四项可靠性指标。

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
| `download_99ranch_flyer.py` | 99 Ranch 自己的接口 |
| `parse_99ranch_deals.py` | 99 Ranch 的解析（Flipp 的抓取器直接产出成品，不需要单独解析）|
| `deal_fields.py` | 各解析器共用的字段规范化 |
| `import_to_database.py` | 入库 + 调用校验 |
| `publish_gate.py` | **唯一决定数据能否上线的地方** |
| `pipeline_status.py` | 健康状态计算 |
| `database_utils.py` | 批次模型 |
| `migrate_batch_independence.py` | 数据库结构迁移（可重复运行）|
| `run_pipeline.py` | 调度器 |
| `app.py` | 管理后台 |

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

- **单位解析约 9% 有误**（见上方"下次从这里开始"）
- **分类全空**（Flipp 不提供）
- **原价覆盖约 1%**（商家整期只标会员价）
- **选错传单无法察觉** —— 如果两份传单商品数接近，所有检查都发现不了，只有日期区间能看出来
- `parse_compound_deals.py` 目前无人调用，留着给下一阶段处理多件促销用

---

## 协作约定

- 代码和文档进 git；生成的传单文件、日志、数据库、`.env` 不进
- 规划文档在 `../docs/`（仓库之外，按 `YYMMDD_主题_版本` 命名）
- README 和旧笔记冲突时，以代码和本文件为准
