# BP 原语与扩展语义 v0.1

BP（Binding Plan）把只读 DBC 的设备行为和只读目标 FP 的框架能力对应起来。它保存引用、映射、实现选择、派生义务和缺口；设备常量/公式与框架保证不在 BP 独立重写。以下原语描述需要建立的对应，不是自动完成的证明或已执行的工具。

## 根与集成

### BindingPlan(device_contract, framework_profile, target_support, scope, initialization=None, setup=None, primitive_bindings={}, io_buffer=None, requests=None, event_bindings=[], concurrency=None, obligations=None, unresolved=[])

前三项为 Ref，固定父材料及 hash；scope 必须符合任务。路径别名 dbc/fp/support/task 分别指给定设备契约、目标 FP、公开目标支持及任务投影。initialization/setup 组织初始化对应和框架前提；primitive_bindings 为 DBC 操作路径/原语名到绑定的字典，路径级优先于原语级。重叠或缺失映射必须检查，不能把同名原语在不同上下文的参数混合。

io_buffer/requests/event_bindings/concurrency 分别描述资源、请求、事件和并发对应。obligations 为待检查义务而非通过标志。None/省略表示未覆盖；任务所需部分仍有缺口时不能开启完整 generation_scope。unresolved 接受字符串列表或 InheritDBCUnresolvedAndAdd。

### SequentialNativeRequests()

仅覆盖按序发起且无重叠的原生请求。它不改变设备策略，不自动提供忙拒绝逻辑。若原生框架可能重入，必须由公开集成限制或明确 ConcurrencyBinding 建立此前提；否则列缺口。

### BindOperation(device_operation, native_entry, ready_when)

device_operation 指父 DBC 中的行为路径；native_entry 是原生接口或显式计划新建的代码入口；ready_when 是就绪谓词。接口返回“已接受”不等于设备阶段已成功结束。新建入口必须可由公开集成调用，不能凭名字虚构 OS API。

### DeviceInitializationSucceeded()

本 DBC 所要求的初始化及必要数据建立已成功；先验环境条件也成立。失败、提交成功或 enable 完成不能替代设备初始化成功。

### EnsureFPRequirements(**bindings)

键是当前 FP 前提/设置项的本地名称，值是有证据的原生入口、集成提供项或明确未决。开放键不授权添加框架事实。检查设置顺序、实例和生命周期，不能把设备初始化命令塞入此字段。

每项绑定须能回答：谁负责、由哪个公开入口/事件及守卫触发、对哪个实例调用什么或初始化哪些字段、有效参数来自何处、哪个成功事件建立哪个后置状态、失败如何终止或回滚。使用已有 Sequence/FSM、绑定节点或可读正文表达，不要求新增构造字段。仅“检查前提”不能替代建立它的动作；分配对象后，不能将零值默认为合法的注册/配置元数据。所有消费该前提的路径都应先完成相应建立动作，并说明关闭、失败和重入时何时失效。

责任方可为驱动、具有原文保证的公开集成、明确标注的条件环境前提或未决；不能没有集成证据就默认由外部完成。原始框架检索可补充具体入口、字段和域的证据；若与只读 FP 冲突或需要补充保证，应记录为 FP 定向修复事项，不在 BP 另立一份框架契约。

### ProvidedByTargetIntegration()

引用公开集成对当前前提的保证；必须能定位集成证据。它本身不建立保证；若公开材料没有该保证，形成未决而非生成器默补。

## 调用与事件

### BindAsyncCall(call, completion, payload=None, length=None, on_success=None)

call/completion 是父 FP 中的调用及完成事件；payload/length 引用当前被绑定设备动作的数据。检查原生接受、拒绝、成功与失败路径和资源转移。on_success 只在当前事务完整成功后发生。调用存在及 callback 存在不证明时序对应。

### BindSyncCall(call, payload=None, length=None, on_success=None)

同步对应；根据 FP 的 SyncReturn 解释成功返回、错误与资源效果。必须另检查返回是否足以观察 DBC 所需的物理事件，不能只因 API 阻塞而成立。

### FromDBCOperation()

取当前 DBC 操作的参数/载荷，不复制维护另一套设备常量。若绑定覆盖多个操作，按每个操作分别实例化；不能取第一个操作参数用于全部操作。

### FromDBCFrameLayout()

取当前读操作引用的唯一布局长度；目标容量只提供上限，不改变设备实际帧长度。

### DecodeAndValidateFromDBC()

必须按当前 DBC 的字节布局、符号和完整性定义解码、验证，并保留本轮身份。此节点要求实现相关步骤，不能作为“已校验”标志。

### EventBinding(device_event, native_event, relation, assumptions=[])

建立 EventRef 间的关系。relation 为 equal、device_not_after_native、native_not_after_device 或有证据的时间区间关系字符串；不能只写“对应”。等同是最强关系，必须单独有据。当前操作身份、成功/失败、provider 条件及 assumptions 均需成立。不能将上一轮 callback 或成功之外的完成满足当前等待。

### SuccessfulCallbackOfReferencedTransaction()

选择当前 WaitAfterCompletion 所指事务的成功回调作为可观察锚点。只有 FP/事件绑定建立“该物理成功不晚于该回调”时，回调后等待才可给出安全物理下界。若 FP 仅有 ProviderConformanceRequired，尚不能使用为已确立关系。

## 计时与量化

### BindAlarm(anchor, duration, tick_choice, next_step)

把 DBC 的等待映射到目标 alarm。anchor 是有证据的原生可观察事件；duration 引用适用 DBC 界；tick_choice 建立物理等待与目标 tick 的关系；next_step 约束后继动作。还须满足 FP 的最小间隔、计数范围、回绕和 callback 生命周期。不是把毫秒数直接传给一个同名函数。

### BindSyncDelay(call, argument, anchor_call, anchor, duration, next_step, on_interruption="fail_current_operation")

同步延时绑定。call 用 FromFP 指向 completion 为 SyncReturn、delay 为 ElapsedTimeLowerBound 的 APIContract；argument 是该 API 的整数原生时间参数。anchor_call 用 FromFP 指向等待锚点对应的同步 I/O API；其 SyncReturn.physical_completion_order 必须已明确 equal 或 device_not_after_native，不能仅有 ProviderConformanceRequired。anchor 是 EventBinding，引用当前等待的精确设备事件和该同步返回的当前实例，不得夹带未证明 assumptions。duration 用 FromDBC 指向当前等待的 minimum，或用 MaximumApplicableDBCBound 引用该值；不得重写设备时间。延时调用必须在该返回之后，且仅成功路径继续 next_step；中断/错误终止本次操作。检查范围、单位、提前损失和下界；候选代码顺序、中断分支、实际物理 provider 和上界另查，不能由该节点宣称通过。

### MaximumApplicableDBCBound()

对当前输入条件所有可能分支取所需最小等待的最大值。实际条件已确定则只取该分支；部分未知且分支均被覆盖时可保守取最大值。未知/未覆盖条件不能靠最大值消除。只有“至少等待”且不违反上界/新鲜度/周期等约束时适用。

### CheckedPhysicalLowerBound(ticks=None, clock_bounds=None, phase_loss=None, reference_event=None, variables={}, assuming=[])

建立并检查 tick 选择义务。省略参数表示计划由已引用父条目解析/求解，不表示已检查。必须解析或报告：所需最小物理时间 d、频率上界 f_max、最大相位损失 phi（tick）、参考事件关系、原生最小/最大 tick 和回绕安全范围。

在“参考计数读取不早于所选锚点、频率不超过 f_max、相位损失最多 phi、回调不早于 deadline、无额外提前量”的适用模型中，下界为 `(n - phi) / f_max`，因此 n 需满足 `n >= ceil(d * f_max + phi)`。这只是该模型下的充分条件，不能用于频率无界、暂停语义不明或不同 deadline 规则。phi 不默认 0 或 1；据 FP/provider 确定。超过范围、转换饱和或计算溢出时须分段等待并证明新模型，或报告缺口，不能截断后宣称通过。

等待上界若也存在，还需考虑 f_min、调度/回调最大延迟和后继 I/O 启动延迟。只证明不早不能证明不晚。无硬实时延迟上界时，上界义务保持未知。

泛型时钟可用 variables 声明参数的类型及闭区间（与数值域相同的 type/bounds 字典），ticks 和 clock_bounds 内的数值可写这些参数的纯表达式。assuming 为显式适用条件/运行时范围守卫，不能默认为已实现或已由 provider 满足。求解器检查声明域与这些条件下的整数 tick、物理下界和计数范围；结果单独保留条件、provider_assurance=unknown 和 candidate_guard_conformance=not_checked。这只是有条件的算术事实，不证明实际频率、回调/锚点、运行时守卫或硬件；不能用任意假设删去产品必须支持的输入，或把范围不够的截断/饱和当成功。

### AfterAlarmCallback()

只有当前匹配 alarm 回调到达且满足引用的到期条件，才允许推进绑定的下一动作。过时/取消后/重复回调不得驱动新一轮；是否能识别或排除它们需要 FP/实现依据。

## 样本、资源和并发

### BindSampleCommit(storage, validity)

把 DBC 逻辑提交对应到目标存储；storage 选择内部表示，validity 引用父有效性条件。先验证所需字段再一次逻辑替换，不能因目标两个 setter 分开就允许部分新样本外泄。

### CacheValidatedRawFields()

一种实现选择：缓存通过全部要求的原始字段及其有效性/采集身份，按需使用 DBC 物理关系产生输出。不得缓存未校验的 buffer 引用后提前重用，也不把缓存读取当新采集。

### FromDBCCommitRequirements()

引用当前 DBC 提交的完整性、配对、同次采集和失败保留条件，不由目标存储习惯另加“原子性”定义。

### ReuseTargetBuffer(capacity, restore_on, reuse_after)

重用公开支持提供的 buffer。capacity 是实际可用字节数；restore_on 列出所有归还所有权的原生路径；reuse_after 规定数据最后使用条件。设备需要长度不得超过容量；提交被拒、异步成功/失败、取消/关闭的资源效果按 FP 分开检查。列表中写了 completion 不能证明回调真的返还 buffer。

### FrameDependenciesConsumed()

复用前，当前帧的校验、解码及需要保存的数据依赖已消费或已安全复制。若样本/校准仍引用 buffer 内容，此谓词不成立。它不是“回调已到”的同义词。

### FromTargetSupport(path)

读取当前公开支持的已给字段并固定其来源；缺失为未决，不转去隐藏配置。容量、注册和可编辑路径不得由参考实现补出。

### ConcurrencyBinding(policy, synchronization, request_identity, stale_events)

policy 只可实现任务/DBC 允许的序列化、忙拒绝或共享采集关系；不重新选择设备策略。synchronization 引用目标 FP 的锁/中断约束等能力，request_identity 和 stale_events 说明请求/事件归属及过时事件处理。逻辑样本配对不能证明 CPU 级互斥；原生共享是否允许必须有任务依据。

## 请求、错误与数值

### BindRequests(native_entries, device_operation, acceptance, success, failures)

绑定原生接口请求与一个设备操作。acceptance 定义何时原生请求被接受；success 定义本请求的成功交付；failures 是 `(接口名, before_acceptance/after_acceptance)` 到处理方式的字典。每个被接受请求如何结束必须有据，不能凭模板默认一次回调。共享一次设备采集服务多个接口时，需另说明请求归属及允许共享的范围。

### FromFP(path)

引用父 FP 中的能力/类型/输出条目，不复制另一份接口定义。若 FP 未覆盖或版本不匹配则未决。

### FromDBC(path)

引用父 DBC 中的行为、布局或物理关系，不重写其内容。仅适用于当前已选策略和条件。

### WhenNewAcquisitionHasBeenAccepted()

原生接受须对应新的设备采集已经被底层接受，而不是仅有缓存或计划稍后重试。若目标调用发生部分接受，必须明确哪一步作为边界。该选择是否符合原生 request API 和任务语义另行检查，不作为所有驱动的统一强制规则。

### DeliverRequestedQuantity(physical_relation, encoding, rounding)

从本请求所关联的合法样本按 DBC 物理关系计算，再按 FP 编码交付所请求的量；不能用旧样本伪装本次采集成功。整数/浮点算法由生成器选择，但需满足任务精度、原生类型、合法范围及溢出义务。

### FromTaskPrecisionRequirements()

只引用当前任务给定的误差/舍入要求；任务没规定精确舍入时，可在允许范围内提出明确实现选择，但不能把未知误差上限宣称为“目标可保留的精度”。缺失精度要求与“允许任意误差”不同。

### NumericEncoding(physical_relation, encoding, rounding, error_bound)

显式数值方案：理想 stored 值为 `(q - offset) / scale`，按 rounding 转到 FP 存储域；error_bound 为物理单位下的最大允许偏差或引用。需检查 `abs(stored * scale + offset - q_reference) <= error_bound` 及量纲/比例、合法输入全域、符号、端点、算法误差和中间溢出。理想整数编码的 `abs(scale)/2` 误差界只在最近舍入且无饱和/算法误差时成立；实现近似计算误差须额外计入。

### Rounding(mode, overflow="report_gap")

mode 为 nearest_ties_even、toward_zero、floor、ceil；明确对应数学取整规则。overflow=report_gap 表示超域不适配，不能自行 wrap 或 clamp。仅当任务/接口明确允许时可用 saturate，并说明饱和误差不再有一般半单位界。未给 mode 时不能从 C/Rust 默认转换推断任务要求。

### ReturnError(code)

在原生请求被接受前，以 FP 支持的立即返回渠道返回指定错误。不假装已经提交采集，也不再按已接受请求交付成功。code 必须属于目标接口实际可表达的错误集合。

### CallbackError(code)

在已接受请求的完成渠道交付失败，只有该接口实际支持错误参数/值时可用。没有错误通道时必须保持缺口；不能用零值、旧样本或省略 callback 冒充满足要求。

## 义务派生与缺口传播

### DeriveFromReferencedContractsAndBindings()

此节点要求从引用父模型与绑定派生义务，不表示检查器已运行。至少包括：父引用/条件及策略一致、动作覆盖、事件身份及关系、物理时限、帧和校准有效性、提交/请求对应、接受前后错误能力、所有权守恒、资源最后使用、数值编码、初始化前提、失败恢复与并发/进展范围。各项应保留来源；未实现/缺证据结果为未知。

### InheritDBCUnresolvedAndAdd(items)

合并父 DBC 未决与新增绑定缺口；同一 subject 可引用而不复制全文。FP/provider/公开支持的相关未决也必须经 obligations 传播，不能因名称仅提 DBC 就忽略。没有上游候选更新和新证据时，不允许下游删除它们。

## 一个绑定必须满足的关系

对于目标产生的相关事件/数据轨迹，在已声明条件和假设下，按 BP 映射回 DBC 事件、隐藏内部调度步骤后，应满足 DBC 允许的行为，并满足原生 FP 的前提和保证。只做轨迹子集检查可能把“不接受任何请求”误当正确，因此还需任务要求的可用性/接受范围和进展义务。性能选择和内部状态组织可以变化，父要求不能变化。

当前内核已实现部分时限、帧/资源/请求观察及数值检查，范围见 IMPLEMENTATION_PROFILE.md；没有候选原生观察或未编码的性质继续报告 unknown。完整模型和检查义务已经定义，任何调用不得把这些节点名称当成已通过检查的状态。
