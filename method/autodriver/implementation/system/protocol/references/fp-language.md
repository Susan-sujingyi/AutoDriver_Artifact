# FP 原语目录 v0.1

FP（Framework Profile）描述给定 OS 快照和配置下的框架能力、调用要求与保证。它可共享，但具体 provider、执行上下文及版本条件仍须有证据。下列构造器定义“如何表达保证”，不预先断言任何 OS 实际提供了保证。原始框架证据与实现的适用范围必须逐条核对。

## 根与通用接口

### FrameworkProfile(os, snapshot, i2c=None, spi=None, gpio=None, alarm=None, outputs={}, capabilities={}, execution=None, lifecycle=None, resources={}, assumptions=[], unresolved=[])

os 为 OS 身份，snapshot 为冻结快照/配置身份引用，不能仅写可变分支名。i2c/spi/gpio/alarm 是常用能力槽；其它能力放 capabilities，不能任意增加根字段。outputs 是命名 SensorInterface，resources 是命名资源协议，execution/lifecycle 为相应契约。None 槽表示本次未描述该能力，不表示 OS 没有该能力。假设和缺口须传播到消费者；证据链接置于响应层，可用 Constraint 精细归因。

### APIContract(symbol, parameters, requires=[], accepted=Unspecified(), rejected=Unspecified(), completion=Unspecified(), errors=Unspecified(), resource_effect=Unspecified(), context=Unspecified())

通用 API 条目；parameters 是参数名到原生类型及范围的字典。requires 是前提列表；accepted/rejected 区分接受与同步拒绝；completion 是同步返回或异步 Callback 语义；errors 说明错误渠道。对一个 API 的 guarantees 只在 requires 及配置条件成立时有效。函数名、签名或一个使用示例不足以证明所有 provider 的完成与资源保证。

参数范围须包含实际有效域，不能只记录类型或上界：核对保留值、哨兵、枚举成员、引用的数据表及注册/配置对象必需字段。沿公开实现的调用和数据依赖恢复事实，未选定 provider 或配置的条件仍保留。每项前提还应说明实际建立入口或字段初始化、框架要求的责任角色、输入、成功后的效果、有效期和失效条件。将这些说明放在现有 parameters/requires、相关 API、LifecycleContract 或可读正文中，不新增必填构造字段。列出一个谓词不等于调用者已经完成动作；环境前提与驱动必须执行的动作应分别记录。

### ExecutionModel(style, contexts, may_sleep=Unspecified(), reentry=Unspecified(), synchronization=Unspecified())

style 为 synchronous/asynchronous/mixed；contexts 描述线程/中断/deferred 等原生上下文及其证据。不得由一个同步传感器接口推出整个 OS 都同步。may_sleep、可重入和同步能力须分别声明，不把它们当成设备行为。

### SyncReturn(value, resource_effect=Unspecified(), physical_completion_order=Unspecified(), delay=None)

描述一次同步返回的值、资源效果和已建立的物理事件关系。同步返回本身不等于物理完成，仍须 provider 证据。错误返回和部分效果由 ErrorContract 描述。

delay 仅用于有据的同步延时返回保证；普通总线返回不得自动带延时保证。

### ElapsedTimeLowerBound(argument_unit, argument_range, early_loss, success_condition, interruption="failure")

同步延时 API 在 success_condition 成立的返回路径上，从调用进入到返回至少经历 `argument * argument_unit - early_loss` 的物理时间。argument_range 是有据的闭区间整数参数域；单位须为时间，early_loss 是带单位的非负最大提前损失。成功谓词是只引用 result 的封闭纯表达式。interruption="failure" 表示信号等提前返回不满足成功谓词；未知或允许成功提前返回时保持缺口。原文未给物理下界、参数范围、取整/溢出或中断规则时不得猜测；零 early_loss 也须有据。该节点不是实际时钟精度、硬件或所有 provider 的验证证明。

### Callback(name, arguments, resource_effect=Unspecified(), cardinality=Unspecified(), context=Unspecified())

arguments 为名称到原生类型的字典；cardinality 如需“每个接受请求一次”，必须明确其量词、成功/失败条件及取消例外。默认未知，不能由存在 callback 声明推导 exactly-once。回调携带的数据/资源必须绑定到本请求；过时回调的处理能力另述。

### ErrorContract(submit, completion, partial_effects=Unspecified(), data_validity=Unspecified())

submit/completion 分别列出接受前和接受后错误的原生表达；无某错误渠道时明确为空或 unsupported，不能编造参数。partial_effects 表明部分 I/O 是否可能已发生；错误不能默认保持硬件不变。data_validity 说明失败返回数据能否使用。

## 总线与前提

### AsyncI2CContract(interface, write, read, before_io, accepted, rejected, completion, physical_completion_order, write_read=None, cancel=Unspecified())

I²C 异步能力的紧凑表达。interface/write/read/write_read 是有证据的符号及参数约束；write_read=None 只表示未恢复该能力。before_io 的前提及 accepted/rejected/completion 的资源规则适用于声明的每项调用；有差异须改用 capabilities 中分开的 APIContract，不能用一个摘要掩盖差别。physical_completion_order 仍需 provider 符合义务。该条目不包含具体传感器命令、地址或延时。

### DeviceEnabled()

前提：当前目标总线设备实例已按框架要求启用。必须恢复启用入口与生命周期，不能靠此谓词自动生成未经证实的 enable 操作。

### ClientRegistered()

前提：需要的完成接收者已正确注册，实例/生命周期匹配。不保证调用后必然有回调，也不等于客户端不会被替换。

### CallerOwnsBuffer()

提交前调用者具有该 buffer 的所需独占/可变访问权。仅有指针不证明所有权，所有权转移结束前不得重用。

### TransferLengthFitsBuffer()

传输长度处于 API 合法范围且不超过实际可用容量；同时核对单位、读写两方向和 API 是否允许零长度。容量充足不保证长度满足设备协议。

### HoldsBufferUntilCompletion()

接受后 buffer 归框架持有，调用者不能访问，直到相应完成/取消路径明确返还。它不规定具体 OS 容器，不保证框架最终归还。

### ReturnsErrorAndBuffer()

同步拒绝时返回错误及仍有效的原 buffer 所有权。只有 API 明确未接受才适用；不能套到已经发生部分 I/O 的异步失败。

### ReturnsBufferToClient()

相应完成路径把当前事务 buffer 所有权返还给接收者；不表示其数据有效或可立刻覆盖仍需消费的字段。

### ProviderConformanceRequired(relation)

relation 是调用/物理/回调事件之间需要 provider 支持的关系，如物理成功先于成功回调。此构造器生成未完成的符合义务，不能充当已建立保证。只有当前 provider 的证据/方法内部验证建立关系后，下游才能据其推导物理下界；获准显式假设须标为假设。

SPI、GPIO、DMA、供电控制及中断注册等在 capabilities 中用 APIContract、ResourceProtocol、LifecycleContract 表达，不根据总线名称预置其所有权/阻塞/时序保证。本版不需要额外按 OS 或器件手写一类构造器。

## 时间与计数器

### TickAlarmContract(arm, callback, guarantee, clock_frequency, counter_type, microseconds_to_ticks, minimum_ticks, counter_bits=Unspecified(), frequency_bounds=Unspecified(), cancel=Unspecified(), context=Unspecified())

描述原生相对/绝对计数器 alarm。clock_frequency 为符号/数值及其有效条件，frequency_bounds 是可用速率范围；minimum_ticks 是可调度的最小间隔；counter_bits 决定回绕范围。guarantee 只在 API 接受范围与比较模型内成立；频率漂移、挂起、相位及延迟应独立恢复，未知就不能证明物理等待。cancel 不自动推导为可靠取消旧事件。

### NotBeforeTickDeadline()

相应 alarm 回调不会早于 API 所定义的 tick deadline；不自动保证“至少等待了 dt 个完整物理 tick 周期”。相位、参考计数读取时刻及 provider 限制由 BP 处理。

### FloorAndSaturate(input_unit="us", output_unit="tick", limit=Unspecified())

转换先按定义频率计算理想 tick 数，向下取整，再截断到 API 上限。limit 未建立则饱和范围未知。饱和或向下取整均可能缩短物理等待，不能作为安全下界实现。函数若有中间整数溢出须另作诊断，不能用本抽象掩盖。

## 输出、资源和生命周期

### SensorInterface(request, immediate_result=None, callback=None, callback_value=None, unit=None, asynchronous_error_parameter=Unspecified(), synchronous_return=None, encoding=None)

描述一个原生通道接口。异步接口给出 request、接受/拒绝 immediate_result、callback 及其值类型；同步接口用 synchronous_return。异步错误能力必须按真实签名恢复。unit 为原生数值所代表的物理比例，如“0.01 °C”；若给 encoding，它须与 unit 一致。此处只声明框架可表达什么，不选择传感器模式或缓存/发布策略。

asynchronous_error_parameter=None 可以明确表示没有额外独立的异步错误参数；callback_value 中的 Result/Err 分支仍须忠实保留。原始值是否另有错误哨兵只能按原文恢复，不能从 None 推出不会失败，也不能编造一个 ErrorCode 参数。没有资源转移的接口可用空资源效果集合表达，但不能把持有/归还 buffer 的真实义务标成不适用。

### OutputEncoding(storage_type, unit, scale=1, offset=0, range=Unspecified(), error_representation=Unspecified())

给出规范关系 `physical_quantity = stored_value * scale + offset`，unit 是基础物理单位。scale 是每整数单位对应的物理量，必须非零，不是其倒数。storage_type 给出位宽/有符号性；range 为合法存储域。舍入由 BP 根据任务要求选择，不因 FP 的存储类型自行确定。若 SensorInterface.unit 已含比例，必须规范化核对，不能再乘一遍。

### ResourceProtocol(resource, initial, states, transitions)

一个局部所有权/占用协议，states/Transition 使用公共 FSM 节点；resource 是框架对象身份，不是器件样本。必须覆盖接受、拒绝、成功、失败及适用的取消/关闭路径。逻辑返还与资源实际可访问的时刻不得混同。

### LifecycleContract(setup, ready_when, shutdown=Unspecified(), reentry=Unspecified())

setup 为框架初始化/注册要求，ready_when 为可调用接口的条件；可用公共 Sequence/FSM 表达局部协议。shutdown 和重入不明则保留未知。它只表达框架要求，不保存设备清状态、复位或转换命令。

setup 应给出有据的调用/字段设置顺序、对象身份和参数前提，以及每一步成功事件建立的就绪条件。注册/设置被拒绝、完成失败或部分设置成功时，分别描述后置状态、仍持有的资源及回滚要求。ready_when 引用已成功建立且仍有效的条件，不能仅由“已调用 setup”或“请求已接受”推出。

## FP 检查义务

必须检查版本/配置、符号与 provider 解析、参数类型和上下文、前提可满足性、接受/完成分离、资源守恒、错误表达能力、计数范围及输出编码。局部协议不得与签名矛盾；“没有错误参数”与“不会出错”是两个不同命题。共享 FP 仅接受框架来源闭包，在线缺项形成任务内候选，不自动改写共享版本。
