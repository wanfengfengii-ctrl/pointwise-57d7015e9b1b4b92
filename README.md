# 澄湾混样日仓

白班配样员按计量原单登记实收和混样转装。档案员发行指定修订的封批件，夜班在与源仓断开后用中心来件核对剔批申请。次日复核员按原号领取实际入账口径、固定证据和首执。

## 运行

使用 Python 3.11 标准库。在项目根目录执行 `python3 -B check_project.py` 运行完整回归。该入口随控制台物化提供。执行 `python3 -B -m examples.demo` 演示真实双库交班，样例使用独立临时目录。套件位于 `tests` 包。

## 录入及发行

1. 用两个独立的 `sqlite3.Connection` 分别打开源仓和中心。源仓调用 `initialize_lot_book`、`initialize_operations`、`initialize_publications`，中心调用 `initialize_accepted_batches`、`initialize_settlement`。函数分别位于对应职责模块。重开时可重复初始化，已有记录保留。正式业务入口管理自身事务，调用时连接须空闲。保留 `initialize_settlement` 启用的外键检查。
2. `intake.lots.record_lot(db, lot_id, revision, kilograms, receipt_id)` 返回不可变 `LotRevision`。`intake.source_book.LotBook(db).get_revision(lot_id, revision)` 可查原单及实收。同一原批和修订复投同内容可重领原记录。合法更正使用新修订。身份为非空字符串，按原值比较，须可用 UTF-8 表示且无 NUL。
3. `mixing.operations.record_transfer(db, batch, revision, operation_id, inputs, outputs)` 返回 `Transfer`。`inputs` 是入料引用到工序进秤千克的映射，原批引用写成 `('lot', lot_id, lot_revision)`，已登记容器装料写成 `('load', load_id)`。`outputs` 是新装料号到出秤千克的映射。每个引用表示整份实际装料。部分转装须登记有计量的剩料。物理容器再装料时用新的装料号。日批修订内一份装料只消费一次，同一原批只使用一个明确实收修订。上游先登记，互不依赖的工序可换序。实收与出入秤均为非负整数千克，布尔值无效。工序可用 `None` 录缺秤，状态为 `pending_weighings`。用相同工序身份和流向补缺项，已登记实称保留。各必需称量齐全且相容后状态为 `ready`。登记拒绝未知、重复消费、互相冲突或无法满足守恒的计量。
4. `publication.freeze.seal(db, batch, revision)` 核对该修订的全部登记工序和被引用原批实收，将末端装料作为成品桶。返回 `IssuedPublication`，含可传输的 `data` 字节及原封批 `receipt`。缺秤、无工序或不相容时无发行。封批锁定该修订，重领使用原件。更正另录并封存新修订。可用 `publication.history.PublicationHistory(db).get_issued(seal_id)` 领取固定件，未知号返回 `None`。

## 断源结算和复核

5. `assessment.accept.accept(center, issued.data)` 校验并持久接纳 V1 件，返回 `AcceptedBatch`。V1 外层含固定封批号、日批及修订、末端桶目录、匿名平衡证据和内容指纹。证据中的工序节点和有容量的边表示实称流向。计量原单和中间装料的业务编号留源仓。重投相同规范内容沿用原实收。同号异内容、畸形来件及不相容来件拒绝。独立中心可在关闭源仓后使用已接纳件。
6. `settlement.posting.post(center, request_id, seal_id, lot_id, buckets)` 接收成品桶号列表或元组，要求非空且无重复。未知原批、未知桶和已消费装料无效。对每个可能的整数分配，实收入流、容器出入秤和每道工序逐来源守恒同时成立。组量按同一份分配内的选桶量求和，范围两端均可达。单个桶的端点可由不同分配达到。各桶按桶号排序，申请换序属于同参。
7. `PostResult.status` 为 `posted` 或 `pending`。`decision.bounds` 提供组原量、组边界和逐桶边界。组量唯一才签出 `posting`。余额为组选桶实称之和减唯一扣量。逐桶仅写已证明的单点，实证为零也可入账。待核时 `posting` 为 `None`，无桶账或成功回执。有效终局绑定原请求，重投同参重领，异参拒绝。数据库异常由 SQLite 抛出，提交失败回滚本次绑定、账和回执。
8. 源仓可另发更正件。`settlement.reconcile.retrieve(center, request_id)` 或 `retrieve(center, bill_id, by='bill')` 读取原 `PostingRecord`，核对实际接纳件与金额口径中的千克恒等式。未知号、待核号和失败申请抛出 `KeyError`，未知封批号的结算也抛出 `KeyError`。校验失败和身份冲突抛出 `ValueError`。

## 计量样例

A 为 6 kg、B 为 4 kg，分装为各 5 kg 的 X、Y。对 A 来说，X、Y 各能含 1–5 kg，两桶合起来固定含 6 kg。剔除整组时扣 6 kg，组余 4 kg，逐桶扣账为空。只选 X 时待核。整桶回并为 Z 后，对 Z 可以逐桶扣 6 kg。
