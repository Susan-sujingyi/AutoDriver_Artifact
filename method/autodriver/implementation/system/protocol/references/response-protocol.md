# 自定义响应协议的解释

下面的名称属于 autodriver 自定义协议，不是需要你预先知道的 API。附带的 response_schema 规定 JSON 结构；本说明解释字段用途。两者一起构成当前输出要求。

- `artifact` 是响应 JSON 中装载候选产物的对象，不是文件路径或工具。`artifact.kind` 指这个对象内的 `kind` 字段，不是要求输出一个带点号的键。
- `kind` 是产物类型标签：`fp` 为 OS 框架规范，`dbc` 为给定条件和策略下的设备行为契约，`bp` 为设备行为到目标框架的绑定计划，`code` 为候选代码修改。当前应使用哪个值，由 expected_artifact_kind 确定。
- `format="constructor_tree"` 表示 content 是 JSON 对象：用 `$constructor` 和命名参数写下面定义的同一声明式构造器；具体规则见实施说明。历史 `format="constructor"` 才使用表达式字符串。以当前 output_template/response_schema 选定的格式为准，不能将字符串放进 constructor_tree 的 content。
- `format="constructor_edits"` 只在契约 repair 且主机提供 contract_repair_base 时可用。base_material_id/base_sha256 固定唯一完整可见候选；edits 按命名字段、字典键和零起始列表索引的 path 数组做 replace/add；append 在该 path 所指原有列表末尾追加一个 value，多个追加按响应顺序，原有索引不移动。可同时更新原有元素；其它父子覆盖写、根替换、删除或保留的 $ 载体键仍拒绝。最多32项，未编辑值保持；主机不修数学、不补证据、不改父对象，结果仍过完整语法及语义检查。非法旧候选不能使用此格式，改用当前 schema 允许的完整候选。
- 构造器不表示真实 Python 包，也不要求执行同名函数。`file_edits` 使用 edits 数组，不使用 content 书写整个驱动计划。
- `outcome="candidate"` 表示提供了候选，不表示检查通过；`needs_evidence` 表示请求补充当前权限内的资料；`blocked` 表示本次调用无法继续。后两者可令 artifact 为 null，并填写相应缺口或请求。
- `evidence_links` 使用当前 context 的精确 citation_id 句柄，将具体断言对应到原文片段；主机还原材料 ID、hash 与行区间，不推测缺失内容。旧行号响应保留历史读取能力，新调用按当前 response_schema 写 claim/basis/citations。`unresolved` 记录缺口和影响；`context_requests` 请求有限检索/工具，不授予额外读取权；`change_summary` 给出简短产物摘要。

声明式构造语法使用 `名称(字段=值, ...)`，可嵌套列表、字典、元组、字符串、数值、True、False、None。构造器表示模型节点，不执行代码。禁止 import、属性访问、任意函数执行、循环、推导式及参数展开。引用路径与数学公式写成有定义的字符串数据，不使用 Python 求值。

根名称为 FrameworkProfile、DeviceContract、BindingPlan；每个名称的字段和内部构造器由对应语言参考定义。公共目录定义引用、未知、表达式、事件和 FSM；FP/DBC/BP 目录定义本版各自原语。名字像英文并不等于语义已经确定。未定义的构造器、字段、默认值和事件关系，不能凭词义发明。超出目录时，用 scope=method_specification 的 context_requests 请求扩展说明；若没有足够信息可形成候选，令 artifact=null、outcome=needs_evidence。

本次 user 消息的 output_template 已填好准确的 kind、format、call_id 和 stage，不需要从其它任务示例猜这些值。按 output_instructions 填产物、证据、缺口和请求；完整字段约束在同条消息的 response_schema 中。各类表达式的定义也在本次消息内。
