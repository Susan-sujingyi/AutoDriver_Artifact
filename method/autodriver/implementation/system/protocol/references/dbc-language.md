# DBC 原语目录 v0.1

DBC（Device Behavior Contract）描述输入已经选定的器件条件与策略下，需保持的设备交互和采集行为。它不是器件全部模式的模型，也不是源函数调用树。源 API 的物理效果依赖源 FP 解释；器件要求、已选策略和任务保持的采集语义用 Constraint 区分。原语不是每器件人工类别模板，提取器自由组合公共行为节点。

## 根、连接和条件

### DeviceContract(device, connection, selected_policy, applicable_conditions, behavior, initial_requirements=[], frames={}, quantities={}, calibration={}, cached_read=Unspecified(), invariants=[], progress_assumptions=[], progress=[], unresolved=[])

device 固定变体身份，connection 固定连接约束，selected_policy 引用并规范化已给策略；applicable_conditions 为物理/产品条件字典。behavior 是单一 Lifecycle 或 FSM 模型，不能另维护一份展开图。frames/quantities/calibration 是命名数据定义；其引用必须可解析。initial_requirements 不是已被驱动实现的动作。未建模初始化、故障或重入须列缺口，不能因正常路径短而声称完整保持。

### I2C(address_7bit, clock_stretching=Unspecified())

固定 7 位从设备地址（0..127）和当前器件/策略允许的 clock stretching 条件；不把地址左移后的传输字节当作地址。默认 clock stretching 未规定，不能从连接类型推断启用。设备事务和帧的结束条件仍由动作给出或核实。

### SPI(mode, chip_select, frequency=Unspecified(), bit_order=Unspecified())

固定模式 0..3、器件片选身份及必要频率/位序条件；不保存目标 OS 的 SPI handle、controller 编号或 buffer 类型。CS 跨段保持、读写 dummy 与位标志由事务定义。

### GPIOConnection(lines, polarity, electrical_conditions=Unspecified())

lines 为器件信号名到产品连接标识的映射；polarity/electrical_conditions 为有效电平与必要电气条件。线上的驱动/采样时序属于动作，具体 OS pin API 属于 FP/BP。

### FrozenPolicy(**selections)

开放关键字只用于已有输入中的规范化策略维度，如 mode/channels/rate/repeatability。名称与值必须追溯输入或已固定的源策略，不允许借开放字段选新策略。未确定的产品条件与策略参数分别报告；配置固定并不消除设备失败和环境分支。

### PowerUpCompleted()

前提：当前器件已完成适用条件要求的上电稳定过程。该标记不发出供电或等待动作；必须说明产品保证、已执行步骤或未决。

### ExpectedResetDefaults()

前提：本契约依赖的复位默认状态有效；仅对实际依赖的寄存器/模式成立。不能推断之前没有别的客户改变过器件，也不自动替代 reset。

### BySupply(bounds)

ConditionalValue 的供电专用写法；bounds 是电压区间到值的字典。`a..b V` 双端包含，`a..<b V` 右端不含。条件必须在相关动作/转换期间成立；跨区间变化时取所有可能条件检查，不能只读起始电压。覆盖、交界和最大适用界由消费者处理。

## 事务和时间

### WriteBytes(data, name=None, boundary=Unspecified())

发出完整命令/载荷字节，产生当前物理事务开始和成功/失败事件；只在完整成功后推进 Sequence。name 用于之后事件引用，匿名动作只能被结构引用。boundary 声明 STOP、CS 结束或保持等必要边界；默认未知，不用“写完”猜测协议已结束。此动作不保证目标 API 在何时回调。

### Transfer(segments, boundary, name=None)

segments 为 WriteSegment/ReadSegment 的有序列表，boundary 声明段间 repeated-start/CS 保持与最终结束条件。它定义一个逻辑设备事务中的各段，不能被拆分成会改变设备语义的独立事务。中间任何失败使整个事务失败，不能使用未完整帧。

### WriteSegment(data)

Transfer 内的一段字节输出；它没有独立事务完成事件。设备寄存器地址、SPI read/write 位和 dummy 字节必须在数据/布局中明确，不能由目标总线 API 自行猜测。

### ReadSegment(length, into)

Transfer 内读取 length 字节到命名帧/临时数据；length 是非负字节数且应满足布局。只有整个所需事务成功并验证后，数据才可用于提交。

### WriteRegister(address, value, width, byte_order, name=None, boundary=Unspecified(), encoding=Unspecified())

按器件已定义的寄存器编码写值；width 是值位宽，byte_order 描述多字节值，value 是整数或在已提供状态环境中有定义的精确整数表达式字符串。encoding 为明确字典：address_bits、address_byte_order、read_header、write_header、dummy_read_bytes、auto_increment；header 是逐字节纯表达式的列表，只以 address 和纯常量/允许运算定义头部。操作随后按 width/byte_order 追加 value 字节一次，write_header 不能再次引用 value。没有编码依据为未决；不能直接假设单字节寄存器地址或 SPI 位标志。复杂事务可直接用 Transfer 表达。不能因名称相同推断 read-modify-write 安全。

### ReadRegister(address, width, byte_order, into, signed=False, name=None, boundary=Unspecified(), encoding=Unspecified())

读取指定寄存器值到变量，按 width/byte_order/signed 解码；encoding 同 WriteRegister。读取清零、副作用或锁存需要在行为/条件中保留；不把寄存器默认当无副作用内存。

### UpdateRegister(address, mask, value, width, byte_order, name=None, boundary=Unspecified(), encoding=Unspecified())

语义为“读成功后写 `(old & ~mask) | (value & mask)`”，width 限定补码宽度，encoding 同 WriteRegister。只能在器件允许该读写语义时使用，W1C、读清零和并发修改不能被此原语掩盖；所需不可交错条件是设备交互约束，目标锁等实现由 BP 选择。不存在隐含硬件原子性。

### WaitAfterCompletion(of, minimum, maximum=None)

of 引用当前命名事务的物理成功完成，或明确的 physical_success EventRef。下一设备动作不得早于 anchor+minimum；maximum 如给出还限制最晚时刻。minimum 可为条件化时间，必须覆盖当前环境。失败完成不能触发成功等待，旧事务事件不能满足当前等待。允许更长等待仅在没有上界/其它约束时成立。

### WaitFrom(anchor, minimum, maximum=None)

通用物理/逻辑事件锚定等待；anchor 为 EventRef，DBC 不直接引用 callback。其它规则同 WaitAfterCompletion。它允许表示上电、GPIO 边沿或转换开始后的等待，不把所有时间约束都误写成“命令完成后”。

### DriveSignal(line, level, name=None)

对 connection 声明的器件信号施加电平，产生命名物理边沿事件；建立时间与保持时间用 WaitFrom 等单独约束。不能用瞬时逻辑调用时间代替实际引脚变化时间。

### SampleSignal(line, into, name=None)

在合法时间窗口采样器件线并写入声明变量；采样精度、窗口和有效条件须有证据。目标 API/CPU 定时能力在 BP 检查。

## 帧、校验与计算

### FrameLayout(length, fields, integrity=[], completeness="exact")

length 为完整字节数，fields 是命名 Field 字典；integrity 为 Checksum/其它谓词列表。exact 要求完整长度，不能用 buffer 预留长度代替设备实际返回长度。其它 completeness 值未在本版定义。每个实例绑定当前采集与当前事务，错误/短读不得建立有效帧。

### Field(offset, width, byte_order, signed=False)

offset、width 单位为 bit；offset 从帧首字节最高位按传输顺序计数，byte_order 为 big/little，适用于多字节解码。必须位于帧内，不允许无解释的重叠字段。单字节/非整字节的位序若不符合此约定需显式布局，不能假装字节序已说明所有位顺序。

### CRCProtectedWords(names, encoding, crc)

紧凑布局：每个命名测量 word 后紧接其独立 CRC 字段，各 CRC 只覆盖紧前的该 word。encoding 必须明确 word 位宽/符号/字节序；CRC 字宽由 crc 决定。顺序与 names 一致，展开为唯一 FrameLayout，不另维护布局副本；不适用此排列时用 FrameLayout。

### CRC8(polynomial, initial, reflect_input=False, reflect_output=False, final_xor=0)

8 位 CRC 定义。polynomial 为省略最高 x^8 项的 8 位常量，initial/final_xor 为 0..255；规范实现按字节处理，未反射时从最高位左移并异或 polynomial；反射输入时使用逐位反射的输入字节，reflect_output 决定最终寄存器逐位反射，再 final_xor。此约定固定，不能混用另一库的反射选项含义。被校验字节范围来自布局，不含接收 CRC 自身。

### Checksum(algorithm, covered_bytes, expected_field)

algorithm 为目录内 CRC8 或已定义的纯表达式；covered_bytes 为明确的半开字节区间列表，expected_field 为布局内校验字段。范围和 endian 必须一致。未知校验算法不能用“存在校验”代替；本版不默许名为 CRC16 的未定义构造器，可先以精确纯表达式描述或版本扩展。

### ReadFrame(frame, name=None, boundary=Unspecified())

读取命名布局所需帧并进行完整性检查，只有完整读取与全部 integrity 成功才产生有效当前帧。检查中间态可由 FSM 展开查看；不得在校验前提交。boundary 同事务规则。读取错误与校验错误是不同失败原因，但都不能产生本轮新成功样本。

### PhysicalQuantity(expression, unit, requires=[], domain=Unspecified(), bit_width=None)

定义一套规范物理计算关系，expression 引用已定义的原始字段/校准量，按公共精确数值语义计算。requires 保证依赖数据有效且属于当前器件/配置；domain 给出关系有效域。源近似整数算法只作为证据与一致性检查，不是第二套可自由选择的规范。输出存储类型、比例和舍入属于目标 FP/BP。

可执行 requires 是封闭纯布尔表达式字符串列表，语法与守卫相同；它们在提交快照和使用时分别检查。domain 可为同一纯布尔表达式，或 `{输入路径:{'type':'int'或'real'或'bool','bounds':(下界,上界)}}` 的有界映射。输入、单位、位宽和适用域须来自获准资料。自然语言如“数据有效”“某补偿中间量”不是可执行谓词，仍保留 unknown；不能删掉它们或用 True 代替来打开门禁。命名关系依赖在求值时递归检查其自身前提，数值结果没有隐含的 `.valid` 字段。

解释器提供 `frame_status.<帧名>.complete_current` 布尔元数据：只有以该帧名保存、属于本次 operation instance、且已由声明布局完成长度/完整性解码的帧为 True；声明但缺失、旧 instance 或身份不匹配的帧为 False，部分帧/校验失败不产生成功解码帧。`frame_status` 是保留命名空间，不能由 FSM 变量或普通字段冒充。该元数据只表明当前完整解码，不建立物理新鲜性、供应商保证、数值哨兵合法性或校准生命周期。校准跨采集保存、失效和有效标志仍须用有来源的显式状态/赋值及谓词建模，不自动添加 initialized/valid=True。依赖过期校准原始帧的条件不能借此转换为本次有效。

quantities 中可命名共享中间关系，显式用 `quantities.名字` 引用；没有帧/FSM 同名输入时，也可用无歧义短名。依赖必须无环且有界。无量纲中间关系按同一精确算法求值；有量纲关系的同单位纯别名可直接使用，尚未编码的跨单位代数保留 unknown。帧/FSM 变量与短名重合时必须明确限定，不能任选一方。展开表达式只是求解视图，不是另一份规范。命名关系不会使旧帧或失效校准值变成当前有效数据。`/` 是精确有理除法，不能因源代码采用 C 整数运算便默认截断；位运算的显式位宽要求仍适用。

bit_width 若给出，必须是有来源的 1～4096 位整数，限定本条表达式的位操作；None 不猜测位宽。每个命名关系独立声明，不从消费者继承。位操作数及结果须在 `[-2**(bit_width-1), 2**bit_width)` 内；左移不自动回绕，右移是数学整数的向下取整移位，补码取反显式保留 bit_width 位。普通有理数算术不因此变成 C 机器运算，`/` 仍不截断，也没有默认溢出、精度或校准保证。源有限精度计算与规范关系的对应仍须单独核对。

### CalibrationData(layout, read_by, valid_when, invalidated_by=[])

layout 为命名帧/字段，read_by 为本 DBC 中读取操作引用，valid_when 为有效性条件。校准值归属于器件实例及适用配置；复位、模式或电源变化是否失效由 invalidated_by 明确。空列表表示未增加已知失效事件，不证明永不失效；必要生命周期未知须列缺口。不得加入测试后真机校准经验。

## 生命周期、采集和失败

### Lifecycle(initialization, acquisition, on_failure=Unspecified(), recovery=Unspecified(), reentry=Unspecified(), variables={})

初始化成功后才可开始依赖它的 acquisition；每次采集创建新身份。on_failure 为最近未处理失败的逻辑处理器；recovery 明确失败后如何恢复及前提，默认没有可证明恢复边；reentry 声明重入范围。它的展开 FSM 属于本 DBC，不绑定目标函数、callback、TakeCell 或锁。

variables 与显式 FSM 的 type/initial 声明完全相同，声明同一全局图中的持久状态，不是第二份状态机。Sequence 中可使用 Assign，从当前已成功解码帧或状态显式写入这些变量；一次新 acquisition 会清除旧临时帧/寄存器，但保留显式状态。初始化、复位、失败后的有效性必须由有据的赋值/守卫表达；声明变量或初值不自动证明校准有效，不能从旧帧布局补出值。

该组合器的逻辑输入精确命名为 `EventRef('initialize','logical_success')`、`EventRef('acquire','logical_success')` 和上一轮结束后的 `EventRef('next_request','logical_success')`。它们不是物理上电或器件启动事件。I/O 事件的 operation 使用动作明确给出的 name（没有 name 时才用派生路径），不能自行增加 initialization./acquisition. 前缀；等待和 Deadline 必须引用同一全局图里存在的精确 operation/phase，当前发生身份仍在解释时核对。所需物理上电事件没有来源/声明时保留缺口，不能凭空定义一个 initialization.physical_start。

### CommitValidatedSample(from_frame, quantities=None)

从本轮完整有效帧建立新已提交样本；quantities=None 表示该布局所支撑且任务选定的全部量。所有被保持的同次采集数据先整体有效再逻辑提交。一次 logical commit 不等于目标 CPU 线程原子写入；并发可见性由任务约束与 BP 实现。提交不能使用上一轮帧或部分新数据混入旧值。

### FailCurrentOperation(preserve_committed_sample, recovery=Unspecified())

结束当前初始化/采集为失败，不发出本轮成功。preserve_committed_sample=True 时保持旧样本，但旧样本不冒充本次新成功；False 也不自动定义如何使旧样本失效，须有其它明确效果。recovery 只声明有证据的恢复行为，不默认回到 ready，失败后的器件状态可能未知。局部明确 recovery 覆盖 Lifecycle 的同类恢复；均未恢复时没有默认边，不能把两处处理器串行执行两次。

### ReadCommittedSample(when_empty=Unspecified())

读取既有已提交样本，不触发新设备采集，不生成新 acquisition 身份。尚无样本时的行为由 when_empty 指定，未知不能默认为零、旧内存或成功。目标接口是否允许缓存语义由任务与 BP 检查。

## DBC 的完整性要求

控制使用公共 Sequence/Choice/Repeat/WithFailure/FSM，不需要先人工写类别模板。状态/变量必须恢复其设备含义，不能逐目标 OS 状态枚举照抄。给定配置决定策略范围，但无法自动消除失败、环境变化、就绪轮询和重入分支。初始化、短读、校验失败、设备状态失效及进展前提必须逐项有模型或显式缺口。

任何参数默认 Unspecified 都是覆盖债务，不是规范宽松项。完整模型需有事务边界、变量定义、数据/校准身份、时间锚点与上下界、分支条件、成功/失败和适用前提。实现只能在契约允许空间内选择机制，不能替未知路径创造设备恢复协议。
