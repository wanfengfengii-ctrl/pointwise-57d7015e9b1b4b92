# 跨台离线扣量券

README.md 说明原始实收、冻结和剔批业务。现有运行清单先演示 V1，`python3 -B -m examples.voucher_fixture` 用独立数据库演示两台怎样从同一份冻结件各签出 6 kg。这里约定新增扣量券的调用及业务结果。原来的剔批调用和成果仍可独立使用，券账另查。

## 签发与承诺

在 `publication.history` 提供以下操作，沿用原 `initialize_publications(db)` 初始化发行库。

```python
issue_voucher(db, voucher_id, seal_id, lot_id, buckets, station_id, kilograms) -> bytes
confirm_return(db, return_data) -> dict
```

每份封批由一个权威源仓发券。`issue_voucher` 从该源仓已发的确切 V1 封批出具固定券件，固定封批号及指纹、原批号、桶组、目标台、券号及额度。券号在源仓内唯一。`buckets` 沿用 V1 的非空列表或元组及标识规则，申请换序属于同参。额度和下面的办理量都是正整数千克，布尔值无效。

新申请的联合资格涵盖同一确切封批、同一原批的全部未退承诺及本次申请。每张旧券的未退承诺等于初发额度减去源仓已成功确认的交回量，已经实扣的部分仍在承诺内。在冻结件容许的每一种完整整数分配中，都须能给各券分配其未退承诺量，每张券只能使用所选桶组中的该原批公斤数，同一公斤只能分给一张券。对有效新申请，联合资格满足即发券，否则拒发。不同相容分配可以使用不同的兑现分摊。发布的实扣组账也无需固定组内分摊。

`confirm_return` 接收目标台成功交回后产生的固定交回件，核对它对应的原券与目标台，成功返回字典，键为 `station_id`、`request_id`、`voucher_id`、`kilograms`。源仓按实际成功确认的单据释放对应券的额度。后办的单据可先确认，也可在较早单据到达前使用它已释放的容量。源仓以目标台和办理号识别已确认单据。重投原件只复领首次确认结果。发行和确认各自在本地事务内固定结果并更新承诺。

## 断源办理

在 `settlement.posting` 提供以下操作，原中心库初始化继续沿用 README.md。

```python
initialize_voucher_station(db, station_id) -> None
debit_voucher(db, voucher_data, request_id, kilograms) -> dict
return_voucher(db, voucher_data, request_id, kilograms) -> bytes
retrieve_voucher_action(db, request_id) -> dict | bytes
```

每台使用独立的结算数据库，一个台号只对应一份受管结算库。`initialize_voucher_station` 首次固定台号，重开同台调用无变化，改绑拒绝。台号与办理号沿用 V1 标识规则。目标台先用原 `assessment.accept.accept` 接纳 V1 封批。办理时只凭本地已实收的确切封批及发给本站的券件核对资格。券件和交回件经可信通道在两端传递，可延迟、倒序、重投或丢失回复。两端各自提交且不互读数据库。券件与交回件的字节格式由实现决定。

目标台可以按券分次实扣，也可按券分次交回。两种操作共享本台办理号空间。每次实扣或交回只能使用本券尚未实扣且尚未交回的额度。实扣返回固定的组账首执，字典键为 `station_id`、`request_id`、`voucher_id`、`seal_id`、`fingerprint`、`lot_id`、`buckets`、`kilograms`，其中 `buckets` 为桶号升序元组。交回成功立即减少目标台可用量并返回固定交回件，尚余的量仍可实扣。实扣结果或交回件与本台额度变化同次提交。`retrieve_voucher_action` 按办理号返回原组账首执或原交回件，未知办理号抛 `KeyError`。

签发、实扣、交回和确认成功后，同号同参重投和重开重投均返回原结果，即使后来额度已经变化。改绑、格式无效、错券、错台、缺少对应实收件及超额用 `ValueError` 拒绝，源仓请求不存在的封批用 `KeyError`。数据库异常沿用 SQLite 异常。拒绝或提交失败无本次新增绑定、首执或额度变化，合法请求可重试。运行期间无须跨封批合用额度、整券撤销、台间直转或逐桶实扣追踪。

## 演示与核验

`examples.voucher_fixture` 的 `open_source`、`open_station` 可传数据库路径，退出上下文关闭连接，再开原路径可检查历史。`seed` 默认冻结 A＝6 kg、B＝4 kg、X＝Y＝5 kg。传不同 `revision` 可发行同读值的另一版，`a`、`b`、`buckets` 可构造其他守恒样例。`Mailbox.hold` 保存待投件并返回序位，`deliver` 按序位反复投递给所传的收件函数。可在夹具连接上用 `reject_commits` 拒绝块内的 SQLite 提交。

- 在一份封批上给 P 发 X 的 1 kg 券，给 Q 发 X+Y 的 5 kg 券，应成功。另从空发行状态发 X 的 1 kg 和 X+Y 的 4 kg，再申请第二张 X 的 1 kg，应拒绝。在两次独立试办中，先发 X+Y 的 5 kg 并实扣，再尝试只给 X 或只给 Y 发 1 kg，两种试办均应成功并保留原组账首执。
- 用 P 1 kg、Q 5 kg 的第一组券。Q 断源实扣 2 kg，再分别办 R1、R2 各交回 1 kg。确认前源仓拒发额外量。先确认 R2，此时仍不能另发 2 kg。Q 重开，在 R1 确认前实扣余下 1 kg。再确认 R1 并重投 R2，此时可给第三台另发 X+Y 的 2 kg，再加 1 kg 应失败。三台用完各自的量后合计实扣 6 kg，Q 重开可领回两次实扣及两份交回的原结果。
- 检查分属两个目标台的券互换、站内只有同值的另一封批修订、同号换券或跨办理种类。分别在目标台交回及源仓确认时拒绝提交。目标台失败无交回件，也未扣减本地可用量。源仓失败未释放额度。重开后合法重试只生效一次。原 V1 件和账仍照原接口使用。

在 `.pointwise-runtime.json` 中保留现有运行清单格式。让 `verify` 运行完整原回归并核对发券、实扣、交回及重试的结果，任一核验失败以非零状态退出。让 `demo` 实际执行倒序确认后的另发和重开复领，展示各台扣量、确认结果及复领首执。
