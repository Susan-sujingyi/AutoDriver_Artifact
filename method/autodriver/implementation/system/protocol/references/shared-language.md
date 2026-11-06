# 契约语言公共语义 v0.1

本语言是声明式数据，不是 Python API。以下签名定义允许的名称、位置参数、关键字和默认值；签名中的 `*items`、`**entries` 表示构造器接收项，不授权输出 `*list` 或 `**dict` 展开表达式。本版目录是封闭集合，不能自行发明构造器。新增能力须先更新语言版本和语义条目。语义定义完成不代表检查器已实现。

## 值、引用、证据与未知

标量、列表、字典、元组可以直接作为值。字节为 0..255 的整数，长度/次数为非负整数；时间必须带单位，不能将裸数当毫秒。字段名和命名操作在各自作用域内唯一。None 的用途由参数定义；没有定义时不能用它代替未知。省略可选字段不代表对应义务已满足，部分候选必须列出覆盖缺口。

### Ref(identifier)

引用当前获准材料的 material_id 或能唯一定位该材料的虚拟路径；主机固定其 hash。它不读取任意文件。BP 使用 `dbc`、`fp`、`support`、`task` 作为已声明父对象的路径别名；例如 `fp.outputs` 是字符串引用，不是执行属性访问。无法解析或版本不一致为未决，不按名称近似匹配。

### Unresolved(reason)

一个所需值或绑定尚未确定。reason 是非空字符串。它不能满足前提，不能作为任意值由生成器自由选择。

### Unspecified(reason="not recovered")

某行为/保证未建模或未恢复。不同于“允许任意行为”，也不同于“设备无要求”。经过它的路径不得取得完整符合结论。Unresolved 偏向待填的项，Unspecified 偏向行为覆盖缺口，两者都传播未知。

### Constraint(value, origin, evidence=[], under=None)

为一个值或行为增加来源标签。origin 仅为 device_requirement、selected_policy、preserved_acquisition、framework_requirement、implementation_choice。evidence 是当前响应 evidence_links 的 claim 文本/标签列表，同一 claim 可以有多处证据；under 为适用谓词，None 表示继承所在条目条件。标签本身不是证据；实现选择只有被任务采纳后才成为保持义务。未标注时来源为未知，不能默认全部为硬件要求。

### Assumption(condition, scope="current")

显式环境或外部依赖前提；不能由它将已知冲突洗成保证。关键假设须获任务允许并进入检查结论。scope 是它适用的对象/阶段路径。

### ConditionalValue(cases, otherwise=Unspecified())

cases 为谓词字符串到值的有序字典。每个适用输入必须恰好匹配一个分支；重叠或未覆盖为冲突/未知，不默认优先第一个。未知条件保留所有可能分支。不能在使用它时静默选择最快、最宽松或最符合源码的一项。

## 表达式与量纲

表达式作为字符串数据保存。允许变量/字段路径、整数和有限小数、有理数运算 `+ - * /`、整数次幂 `**`、比较、`and/or/not`、括号及 min/max/abs/sqrt/exp/ln；sqrt 输入须非负，ln 输入须为正，实数关系的 exp 不隐含浮点近似。位向量解码另允许 `& | ^ ~ << >>`、整数除法 `//`、取模、`extract(value, offset, width)`、`signed(value, width)`、`unsigned(value, width)`。调用只限这些已定义的纯运算，不允许 Python 执行、任意调用或属性反射。自然语言断言可以保留，但自动检查结果为未知，不能伪称已求解。

`/` 在物理关系中为精确有理/实数除法，`//` 为向负无穷取整；源语言的向零截断不得偷换为此语义。位运算的宽度及有符号性必须由数据定义确定；越界移位、溢出、除零和超定义域均须诊断。物理计算不隐含机器整数溢出。目标整数算法和舍入由 BP 约束。

时间规范化为有理秒；频率为 Hz，时间不得和 tick 直接比较。采集量的输出单位由 PhysicalQuantity 声明；其数值常数按该关系定义解释，不能仅靠常数推断完整量纲证明。温标偏移和百分数比例须显式换算。当前可执行子集与具体参数形式见 IMPLEMENTATION_PROFILE.md；未编码的数学、量纲域和求解问题保持 unknown。

## 事件和数据身份

### EventRef(operation, phase, instance="current")

operation 为命名操作路径；instance 为当前操作/采集的身份，不能隐式指向上一轮。phase 属于物理事件 physical_start/physical_success/physical_failure，框架事件 submit/accepted/rejected/callback_success/callback_failure，或逻辑事件 logical_success/logical_failure/commit。必须遵守所在层可观察的事件：DBC 不以原生 callback 为设备时间锚点，FP 不凭空保证物理完成，BP 负责关系。

“发起提交”“框架接受”“物理开始”“物理完成”“回调”分别是事件，不默认相等。每个帧、校准值和提交样本带产生它的器件、配置版本和操作身份；未经证明不跨轮复用临时数据。

命名帧字段和 ReadRegister/SampleSignal 的 into 名称声明相应数据输出；FSM 其它变量须在 variables 声明类型/初值。名字仅在其数据已成功产生的路径上可用。多个帧含同名字段时必须使用完整路径；PhysicalQuantity 中的短名只在唯一可解析时成立。不存在“名字出现了就已有有效值”的规则。

## 通用控制与 FSM

### Sequence(*steps)

依次执行；前一步成功才进入下一步。失败向最近的失败处理器传播；未规定的失败进入未知结果，不能走下一步。它的状态机为各子模型顺序连接，不要求具体目标 OS 使用同步函数。

### Choice(*cases, otherwise=Unspecified())

cases 为 Case 节点，按顺序测试并选择第一个真守卫，类似 if/else-if。守卫尚不能求值时保留相应路径；不能替模型任意取值。otherwise 处理均不匹配情况。与 ConditionalValue 的互斥条件映射不同，此处优先级本来就是行为的一部分。

### Case(guard, body)

guard 为可解析布尔表达式或显式未决值；body 为行为模型。检查变量定义、作用域和路径条件。守卫不产生设备 I/O。

### Repeat(body, until, maximum_attempts=None, deadline=None)

先执行 body，再检查 until；假时继续。maximum_attempts 若给出是正整数且包含第一次执行。deadline 若给出为 Deadline；达到任一上限但 until 未真时，循环以超时/次数耗尽失败。None 表示该限制未声明，不自动增加无限重试策略；无终止上限时必须另说明进展依据。轮询间隔由 body 中的等待表达。

### Deadline(anchor, maximum)

从显式 EventRef 到当前时刻的最大允许间隔；maximum 为带单位的时间。循环不能在已知截止后再启动新的轮次；截止与完成同刻的优先关系若影响结果，须由所选策略说明，否则未知。尚在进行的总线操作不能因为软件超时就推定已取消。

### WithFailure(body, on_failure)

为 body 增加局部失败处理，覆盖外层处理器。它不自动重试或清除器件状态；处理器成功仅表示处理完成，不把原采集改成成功。若允许恢复后重新采集，须另有带前提的明确路径。

### FSM(initial, states, transitions, variables={}, accepting=[], failing=[])

显式扩展状态机。states 是 State 列表；variables 为变量到类型/初值声明的字典；transitions 为 Transition 列表。initial/accepting/failing 引用状态名。多个可用边必须有互斥守卫或明确的环境非确定性解释，否则报告歧义。组合式模型与 FSM 是同一语义的两种写法；同一行为不能另写一份独立转移表维护。

### State(name, invariant=True)

一个命名控制状态及驻留谓词；布尔 True 表示未增加驻留条件，不表示设备处于任意安全状态。状态不得仅凭相似名称合并。

### Transition(source, event, target, guard=True, effects=[], action=None)

匹配当前状态、事件身份和守卫后消费该事件，执行 action（如有），成功后应用 effects 并转移。非瞬时 action 展开为中间子状态，不能在其尚未完成时到达 target；失败须显式失败边/处理器。effects 是 Assign 列表；跨变量更新视为同一步逻辑更新，不隐含 CPU 多线程原子性。event 必须来自已声明动作、输入或环境；不能虚构可观察硬件事件。

### Assign(variable, expression)

写入本模型已声明变量。整组 effects 使用应用更新前的同一份变量快照（包含该事件数据及 action 成功产生的数据）求值；同组不能重复写同一变量。不能借此执行任意 I/O、旁路帧校验或重写父模型常量。

Assign 也可作为 Sequence 中的显式单步状态更新，仍使用同一图的已声明变量和同一类型检查；多个这样的顺序步骤按顺序生效，不冒充 Transition.effects 的并行更新。失败路径不会越过失败动作执行后续赋值。

### Invariant(condition, scope="all")

声明在适用状态/路径上始终应成立的安全性质；条件不成立为违反，尚不能判定为未知。声明不是证明。设备值有效性和软件互斥必须分开描述。

### Progress(when, eventually, assuming=[])

在 when 成立且 assuming 中环境进展前提持续满足时，最终到达 eventually 指定的结果/状态。它不是硬截止时间，也不能由有限成功轨迹证明。事务返回、调度和时间推进等假设必须显式保留。

## 检查义务的共同规则

所有模型节点均继承器件条件、冻结策略、父版本和证据范围。检查结果分满足、违反、未知；未实现的检查属于未知。语法、参数、证据引用、原语前提、覆盖和行为符合是不同层次。没有证据的保证、任意省略的失败边、无法解析的引用与未建模的路径，不能因模型能解析而消失。
