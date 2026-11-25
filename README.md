# 研发证据转化与传播表述复核系统

一个仅依赖 Python 3 标准库的证据—审批—商品版本—页面物料追踪应用。它把以下对象登记在同一条可审计链路中：

- 科研论文、可编号/可挂载的实验方案（`Protocol`）、实验结果与不可变实验版本；
- 原料、原料批次，以及 R&D 明确确认的浓度、条件和批次适用范围；
- 配方版本、配方继承关系、商品版本、产品/销售批次及负责人确认；
- 表述文本版本、证据引用版本、合规审批的渠道/期间/商品范围；
- 页面、物料、发布记录、活动和发布时不可变快照；
- 证据推翻、配方升级、审批到期等产生的复核案件与处理记录。

系统**不会**因为原料名称、关键词或 `result=positive/replicated` 等文本相似就推断证据支持关系。所有支持关系必须来自显式的实验版本、适用范围、表述证据引用和合规审批。关系或版本缺失时，会输出 `missing_evidence`，相关页面不能显示为 `valid/current`。

## 快速运行

```bash
python3 -m unittest discover -s tests -v
python3 app.py
# 或显式指定复核日期：
python3 -m evidence_governance sample-report --date 2026-11-25
```

生成完整的端到端合成登记处（包含有效页、被新实验推翻的页面、配方升级待复核页、到期页和已结束活动）：

```bash
python3 -m evidence_governance seed-demo registry.json
python3 -m evidence_governance dashboard --registry registry.json --date 2026-11-25
python3 -m evidence_governance reconstruct --registry registry.json --campaign CAMP-SUMMER
```

所有命令都支持 `--json`，便于接入其他系统或自动化测试：

```bash
python3 -m evidence_governance sample-report --date 2026-11-25 --json
python3 -m evidence_governance guide --registry registry.json CL-HYD --date 2026-11-25 --json
```

## 命令行能力

| 命令 | 用途 |
| --- | --- |
| `sample-report` | 直接读取仓库 `samples/`，执行证据评估和使用中页面影响扫描 |
| `import-samples out.json` | 将 CSV 样例转换为 JSON 登记处 |
| `seed-demo out.json` | 写入覆盖主要业务分支的完整合成数据 |
| `dashboard --registry F` | 扫描推翻、配方变化、到期、缺口并列出页面/物料状态和案件 |
| `claim C --channel ... --product-version ...` | 检查某表述在指定日期、渠道、商品/配方/批次下是否可作为已验证表述使用 |
| `guide C` | 列出已批准表述适用的商品版本、配方版本、渠道和有效期间 |
| `using C [--include-ended]` | 列出仍在使用或历史上引用该表述的页面/物料 |
| `publications --registry F` | 列出全部页面/物料及 `valid`、`needs_review`、`blocked`、`ended`、`removed` 状态 |
| `resolve-case CASE --by ... --action ... --note ...` | 产品负责人下线、替换或确认旧版本保留 |
| `confirm-applicability APP --by ...` | R&D 确认实验的原料批次、浓度和条件适用范围 |
| `confirm-lineage FORMULA --parent P --by ...` | 产品负责人确认配方继承关系 |
| `confirm-batch BATCH --by ... [--parent P]` | 产品负责人确认销售/产品批次继承关系 |
| `reconstruct --publication P` / `--campaign C` | 还原发布时文本、审批范围和证据版本快照 |

复核动作包括：

- `removed` / `take_down`：停止使用页面或物料；
- `ended`：标记使用结束；
- `replaced --replacement NEW_PUB`：只有替代发布物当前为 `valid` 才允许结案；
- `legacy_confirmed`：仅适用于配方升级/旧配方后继版本案件，并且旧表述本身仍满足审批与证据条件；
- `evidence_supplemented`、`scope_corrected`、`reviewed_no_change`：记录处理决定；如果扫描发现同一硬问题仍存在，案件会重新打开，不能靠人工备注静默消除。

## 当前显示状态的含义

- `valid`：审批在日期、渠道、商品/配方/批次范围内有效；证据版本存在、未被推翻；原料、批次、浓度和条件匹配或已经 R&D 明确确认；没有未处理影响案件。
- `needs_review`：存在必须补齐或确认的证据/继承关系，或配方已升级但尚未完成旧版本范围复核。此时不能静默作为当前有效页面展示。
- `blocked`：存在硬性冲突，例如无有效审批、审批过期/撤销、渠道未批准、商品或配方版本超出审批范围、实验已被推翻、原料或批次明确不匹配。
- `ended` / `removed`：活动、页面或物料已结束/下线，只保留历史记录，不作为当前展示。

## Python API 示例

```python
from evidence_governance import GovernanceService, Registry
from evidence_governance.evaluation import evaluate_claim_use

svc = GovernanceService(Registry())

svc.register_material("M1", "原料 M1")
svc.register_material_batch("B12", "M1")
svc.register_experiment(
    "E1", version=1, material_id="M1",
    concentration="2%", condition="lab-24h", result="positive",
)
svc.register_applicability(
    "E1", "M1", applicability_id="APP-E1",
    scope="material_wide", concentration="2%", condition="lab-24h",
    confirmed_by="rd-chen", confirmed_on="2026-11-20",
)
svc.register_formula("F2", components=[{"material_id": "M1", "concentration": "2%"}])
svc.register_product("P1", "商品 P1")
svc.register_product_version("PV-P1-F2", "P1", "F2")
svc.register_material_lot_use("PV-P1-F2", "M1", "B12")

svc.create_claim("C1", "仅在实验条件下成立的表述。")
svc.link_claim_evidence("C1", "E1", 1, applicability_id="APP-E1")
svc.approve_claim(
    "A1", "C1", "compliance-li",
    valid_from="2026-11-25", valid_to="2027-11-24",
    channels=["web"], product_version_ids=["PV-P1-F2"], formula_version_ids=["F2"],
)

report = evaluate_claim_use(
    svc.registry, "C1", "2026-11-25",
    channel="web", product_version_id="PV-P1-F2",
)
print(report["usable"], report["missing_evidence"], report["blockers"])

dashboard = svc.review_dashboard("2026-11-25")
print(dashboard["open_cases"])
svc.registry.save("registry.json")
```

## 关键治理规则

1. **证据按版本冻结。** 审批保存 `experiment_id + experiment_version`、立场（支持/反驳）和 `applicability_id`；后续实验新增版本不会自动改变旧审批。
2. **配方升级不自动继承证据或审批。** 新配方版本必须出现在审批范围内，或完成新的证据评估与合规审批。父配方/销售批次继承关系需要产品负责人显式确认。
3. **关键词不参与支持判定。** 即使两个原料关键词相同，只要实验原料不在目标商品版本的显式原料/批次范围内，就产生 `material_scope_mismatch`。
4. **适用条件结构化核对。** 系统核对原料、限定批次、浓度和条件；任一侧缺失时输出缺口，而不是猜“应该适用”。
5. **影响扫描面向仍在使用的资产。** `scan_for_review(date)` 为每个活动发布物生成幂等案件；下线或修复后再次扫描会自动关闭消失的问题。
6. **历史可还原。** 发布时把表述文本版本、审批内容、证据版本和评估结果深拷贝到 `Publication.snapshot`。后续推翻实验、修改审批或结束活动都不会改写该快照。
7. **已结束活动保留发布时依据。** `reconstruct_campaign` 可还原当时的表述、渠道、审批期间和证据版本，用于事后审计。

## 交付的 CSV 样例

示例 ID 和值均为合成数据。CSV 使用 UTF-8，首行为列名，空单元格表示来源未提供值。

- `samples/studies.csv`：科研产出、测试条件、结果和版本。列为 `study, material, concentration, condition, result, version`。
- `samples/products.csv`：原料批次、配方版本和商品版本关联。列为 `product, formula, material, batch, from, to`。
- `samples/claims.csv`：源系统中的表述状态、引用研究、渠道和有效期。列为 `claim, product, formula, study, channel, valid_to, state`。
- `samples/pages.csv`：补充的合成页面/物料清单，用来演示仍在线页面、配方漂移和已结束活动快照。

直接导入原始 CSV 时，系统会保守地保留以下缺口：

- `claims.csv` 没有表述正文，因此生成“不得直接发布”的占位文本；
- `claims.csv` 没有显式证据版本，版本虽可从 `studies.csv` 对应行补全，但标记为 `evidence_version_not_explicit`；
- 没有 R&D 确认人和批次适用范围，生成 `missing_rd_confirmation`、`missing_batch_scope`；
- 没有配方浓度，生成 `formula_concentration_missing`。

因此样例中的状态应理解为：

- `C1/U1`：审批已到期，且 P1 当前已从 F1 切换到 F2，页面阻断；
- `C2/U2`：源状态为 `approved`，渠道和期间匹配 P1/F2，但 CSV 信息不足以完成批次、浓度和 R&D 适用范围验证，所以是 `needs_review`，不是静默 `valid`；
- `C3/U3`：仍在审核中，且研究使用 M-A，而 P2/F1 的显式原料批次关联是 M-B，页面阻断；
- `U4/CAMP-SPRING`：活动已结束，当前不展示，但发布时快照继续保留。

## 持久化与扩展

`Registry.save("registry.json")` / `Registry.load("registry.json")` 使用标准库 JSON 保存全部实体、案件和审计记录。领域逻辑集中在 `evidence_governance/service.py`，评估规则集中在 `evidence_governance/evaluation.py`；未来可在不改变核心规则的情况下增加 Web UI、HTTP API 或数据库仓储实现。

运行原有样例结构测试和新增领域测试：

```bash
python3 -m unittest discover -s tests -v
```
