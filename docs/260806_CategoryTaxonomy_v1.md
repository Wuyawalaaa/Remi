# 分类体系：现状与已查清的部分

2026-08-06 · **未完成**。这份记录已经查清的事实，等 normalization 做完再展开定我们自己的分类。

---

## 一、现状：分类基本是空的

| 店 | 有分类的条数 | 怎么来的 |
|---|---|---|
| 99 Ranch | 113 / 174 | **靠商品名关键词猜** |
| Safeway | 0 / 215 | Flipp 不给 |
| Cardenas | 0 / 216 | Flipp 不给 |
| H Mart | 0 / 48 | Flipp 不给 |

Flipp 的 `category` 字段在**九家商家的全部样本里都是空的**，看起来是全站废弃字段。三家 Flipp 店没有任何现成分类可用。

99 Ranch 那 113 条也不是真分类——代码里读的是 `item_categories`，**这个字段在接口里根本不存在**，永远取不到，于是全部落到商品名关键词的兜底规则上。

---

## 二、关键词猜分类为什么不行 —— rice 这个例子

我们现在的规则里有一条 `rice|noodle|pasta|flour|… → pantry`。

数据里含 "rice" 的商品有 26 种，横跨 7 个真实分类：

| 商品 | 真实分类 | 我们猜的 |
|---|---|---|
| Want-Want Seaweed Rice Cracker | Snacks / Chips | pantry |
| **Keana Nadeshiko Rice Essence Mask** | **Beauty/Health / Facial Masks** | **pantry** |
| Lily Rice Cooking Wine | Condiments / Daily Essentials | pantry |
| Yuki/Love Rice Mochi | Snacks / Jelly-Mochi | pantry |
| 99 Ranch Organic Brown Rice Pasta | Groceries / Dry Noodles | pantry |
| Energy Instant Rice Noodle | Instant / Noodles | pantry |
| Asn/Tas Sweet Glutinous Rice | Canned/Preserved / Fruits-Desserts | pantry |

**一张大米面膜被归进了「干货」。** 26 条全部归进 pantry。

而「大米」本身（Groceries / Grains）**在我们抓到的数据里一件都没有**。

---

## 三、99 Ranch 的两套分类，两个端点

两套编号是两个不同的体系，各有各的取法。**两个端点都只需要一个请求头 `storeid: 8899`。**

### Set A · `categoryViewIds` —— 面向购物者

```
GET https://www.99ranch.com/be-api/product/web/category/list
GET https://www.99ranch.com/be-api/product/web/category/subList?viewId={父类ID}
```

两级树，10 个父类、63 个子类。**这套可用。**

### Set B · `categoryId` —— 内部采购口径

```
GET https://www.99ranch.com/be-api/product/web/detail?productId={任一该分类商品}&storeId=8899
```

返回里的 `backendCategoryId` / `backendCategory` 就是名称。57 个编号全部解出。

但名字是采购缩写、带品牌层级，不是给人看的：

```
CSD Asian Brand · Tea Drink Ch Brand · Juice <=50% OtherBrd
Oth PltMilkYogurtDrk · Potato Chips MnstBrd · Rice Cracker ChTwBrd
```

**这套不用。**

---

## 四、Set A 完整树（10 父类 / 63 子类）

| 父类 | ID | 子类 |
|---|---|---|
| Seasonal | 2 | 20 Mid-Autumn Festival |
| Beauty/Health | 3 | 21 Facial · 22 Facial Masks · 23 Hair · 24 Hair Color · 25 Body · 26 Makeup · 27 Treatments/Supplements · 28 Men's Care · 29 Oral Care · 30 Fragrance · 31 Accessories |
| Beverages | 4 | 32 Drinks/Juice/Water · 33 Soda · 34 TeaLeaves/TeaBags · 35 Coffee/Creamer · 36 Drink Mix/Cereal · 37 Energy/Sport Drinks |
| Snacks | 5 | 38 Candy/Chocolate · 39 Cookies/Cakes/Bread · 40 Nuts/Preserved Products · 41 Chips/Wafers/Crackers · 42 Jelly/Mochi · 43 Soy-based Products · 44 Seafood/Jerky/Egg |
| Instant | 6 | 45 Noodles · 46 Premade Meals · 47 Soup · 48 Desserts |
| Canned/Preserved | 7 | 49 Seafood/Meat/Egg · 50 Vegan/Bean Curd · 51 Fruits/Desserts |
| Condiments | 8 | 52 Daily Essentials · 53 Sauces/Bean Paste · 54 Seasonings/Spices · 55 Soup Base · 56 Dressings · 57 Spreads/Honey |
| Groceries | 9 | 58 Dry Noodles · 59 Vermicelli/Rice Noodles · 60 Grains · 61 Flours & Powder Mix · 62 Gelatin/Tapioca · 63 Dry Groceries |
| Home | 10 | 64 Kitchen · 65 Appliances · 66 Cleaning · 67 Household Supplies · 68 Baby Care · 69 Gardening · 70 Decorations |
| Our Merch | 809 | 810 Clothing · 811 Accessories |

### 本周传单实际用到的 18 个

| ID | 子类 | 父类 | 条数 |
|---|---|---|---|
| 32 | Drinks/Juice/Water | Beverages | 58 |
| 51 | Fruits/Desserts | Canned/Preserved | 24 |
| 33 | Soda | Beverages | 22 |
| 42 | Jelly/Mochi | Snacks | 16 |
| 21 | Facial | Beauty/Health | 12 |
| 39 | Cookies/Cakes/Bread | Snacks | 6 |
| 27 | Treatments/Supplements | Beauty/Health | 5 |
| 25 | Body | Beauty/Health | 5 |
| 41 | Chips/Wafers/Crackers | Snacks | 5 |
| 22 | Facial Masks | Beauty/Health | 5 |
| 45 | Noodles | Instant | 4 |
| 23 | Hair | Beauty/Health | 4 |
| 37 | Energy/Sport Drinks | Beverages | 2 |
| 62 | Gelatin/Tapioca | Groceries | 2 |
| 29 | Oral Care | Beauty/Health | 2 |
| 38 | Candy/Chocolate | Snacks | 1 |
| 65 | Appliances | Home | 1 |
| 67 | Household Supplies | Home | 1 |

按父类：Beverages 82（47%）· Beauty/Health 28 · Snacks 28 · Canned/Preserved 24 · Instant 4 · Groceries 2 · Home 2。

合计 175 个归属 / 174 件商品——有一件同属两类（`[42, 32]`）。

---

## 五、⚠️ 本节已于 2026-08-07 更正

**原标题：「重要发现：99 Ranch 这条线没有生鲜」—— 那个结论是错的。**

原文写的是「整棵树里没有任何生鲜分类，这条数据线上没有菜」。事实是：**99 Ranch 的门店传单每周都有生鲜**，光 Perishables 一页就是 12 格蔬果 + 4 格肉 + 9 格海鲜。

### 错在哪

我拿一个数据源的输出，推断了整条连锁的事实。

| | |
|---|---|
| **原结论的依据** | `storeid 8899` 的 8 个板块、870 件商品，无一件生鲜 |
| **依据本身没错** | 那 870 件确实没有生鲜 |
| **错在范围** | `8899` 不是门店，是 Walong Marketing 的仓库（Buena Park，南加州），线上渠道，`offline: false`，**传单页数 0** |
| **真实情况** | 湾区 16 家门店共用一份北加州传单，20 页约 291 格，其中约 60 格是生鲜/冷藏/冷冻 |

下面的分类树（第四节）**仍然有效**——那是购物者分类体系，本身没问题。错的只是「这家连锁没有生鲜」这个推论。

### 分类体系因此有一个已知缺口

`categoryViewIds` 这套 63 个节点的树里，**确实没有生鲜类目**（没有 produce / meat / seafood / dairy / frozen / bakery）。最接近的两个是加工品：`44 Seafood/Jerky/Egg`（肉干）和 `49 Seafood/Meat/Egg`（罐头）。

所以：**线上商品目录有分类但没有生鲜；门店传单有生鲜但没有分类编号。** 两边不重叠。传单上的生鲜要怎么归类，是个未解问题。

### 数据源的正确取法

```
GET https://www.99ranch.com/be-api/store/web/viewAd?storeId={门店ID}
请求头：storeid: {门店ID}
```

湾区门店 ID：1688 1701 1702 1761 1762 1766 1768 1769 1771 1772 1773 1775 1779 1781 1783 1782
（16 家返回**完全相同**的 20 页传单——一份覆盖整个北加州）

详见 `260807_Ingestion_Design_v3.md`。

---

## 六、待展开（normalization 之后）

**① 不要直接采用 99 Ranch 的父类，理由三条：**

- 父类是货架分区不是食物类别（`Instant`、`Canned/Preserved`、`Our Merch`、`Seasonal` 是店内动线）
- 精度两头都不对：`Beauty/Health` 一个父类 = 我们的个人护理 + 保健；反过来 `Instant`+`Canned/Preserved`+`Groceries`+`Condiments` 四个父类全落到我们的 pantry
- **三家 Flipp 店永远没有这棵树**。采用它等于又拿单一数据源的形状当 schema

**② 正确做法：用子类映射到我们自己的规范分类。** 子类才带着区分度，父类把它抹掉：

```
Facial                  → 个人护理
Treatments/Supplements  → 保健        ← 父类都是 Beauty/Health，分不开
Drinks/Juice/Water      → 饮料
Drink Mix/Cereal        → 干货        ← 父类都是 Beverages，分不开
Premade Meals           → 熟食
Noodles                 → 干货        ← 父类都是 Instant，分不开
```

**③ 要做的事：**

- 63 个子类 → 我们规范分类的映射表，映射不干净的单独列出来让人定，不替人并（例：`Cookies/Cakes/Bread` 同时含烘焙和零食；`Soy-based Products` 算干货还是零食）
- Flipp 三家店的分类从哪来（无现成字段，只能自己推断或另找来源）
- 99 Ranch 是否值得多抓 6 个板块（+693 件，但仍无生鲜）
- 生鲜覆盖怎么解决——这已经超出分类范畴
