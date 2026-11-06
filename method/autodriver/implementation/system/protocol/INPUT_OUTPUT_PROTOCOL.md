# 输入输出协议

## 1. 三类控制信息

本协议区分主机请求、材料登记和模型响应。三者不改变 DBC、FP、BP 的语义分工。

| 对象 | 写入者 | 内容 | 是否直接发送给模型 |
| --- | --- | --- | --- |
| call request | 可信主机调度器 | 阶段、目标、条件、材料选择、编辑范围、预算 | 只发送任务控制投影 |
| context registry | 可信材料准备器 | 材料类型、批准状态、原始路径的虚拟定位、hash、父来源、产生工具 | 不发送完整登记；发送所选材料的必要定位 |
| LLM response | 当前阶段模型 | 候选、证据、未决项、补充请求、修改摘要 | 由主机解析和检查 |

请求的 `schema` 为 `autodriver.llm-call/v0.1`。必填 `call_id`、`stage`、`condition`、`goal`、`selections`、`max_context_bytes`。`condition` 为 S、D、SD 或仅用于框架调用的 FRAMEWORK。S/D/SD 沿用项目源资料/器件资料的消融条件，不是可由模型改变的模式。

`selections` 按顺序列出 `role`、`material_id`，可指定含端点的 1-based 行区间。主机按原生 task.yaml 准备公开任务投影，不建立新任务入口或另一份任务注册表。代码调用另有 `editable_paths`、`base_files`、`host_generation_gate` 和 `generation_scope`；修复另有 `repair_target`。

材料登记的 `schema` 为 `autodriver.context-registry/v0.1`。每项包含 `id`、工作区相对 `path`、`sha256`、`kind`、`approved`、`parents`、`os_role`；诊断另有 `producer`，原文可另有 `origin_locator`。它必须来自可信准备流程，模型不得生成自己的批准登记。原型不验证 release receipt 的签名或真实性。

内部请求不是直接面向模型的说明。装配器将其解释为 task_brief，并给材料附中文用途、给本次响应附固定字段已填好的 output_template 和填写说明。模型不必推测实验条件代号或自行补出协议标签。所有定义和实际材料都在同次消息内，不能靠上一轮对话或只给一个文件名代替。

## 2. 公共响应

所有调用返回单一 JSON 对象，不带代码围栏。其 `schema` 为 `autodriver.llm-response/v0.1`，字段如下。

这些名字是自定义协议，模型不应被假设已了解。装配器自动附带[字段用途与构造语法解释](references/response-protocol.md)、公共语义和当前产物/父契约的语言定义；全部名称见[完整目录](LANGUAGE_SPEC.md)。JSON Schema 只提供语法约束，不能替代语义定义。超出 v0.1 目录的能力须请求扩展。

| 字段 | 含义 |
| --- | --- |
| `call_id`, `stage` | 与当前请求完全对应 |
| `outcome` | `candidate`、`needs_evidence` 或 `blocked` |
| `artifact` | 单一类型的候选产物；没有候选时为 null |
| `evidence_links` | claim、已提供 material_id、原文行区间、简短依据 |
| `unresolved` | 缺口对象、原因、影响 required/optional/unknown |
| `context_requests` | 待回答的问题、获准检索范围、查询及理由 |
| `change_summary` | 简短说明本次建立或修改的内容 |

`candidate` 必须有候选和证据引用，但仍可有缺口。`needs_evidence` 必须有补充请求，可以附部分候选。`blocked` 必须说明未决原因；这里表示本次调用受阻，不代表整个实验或研究目标的终态。模型不能返回 validated/pass/ready。

证据引用只允许指向本次实际提供的行区间。引用有效只证明位置存在，不能证明事实蕴含关系。主机仍须检查关键断言是否有充分依据、证据适用条件是否匹配以及覆盖是否完整。`basis` 是简短依据，不要求模型输出逐步推理记录。材料重复出现时保持同一 ID。

契约产物：`kind=fp|dbc|bp`，当前默认 `format=constructor_tree`，`content` 为同一构造器语言的 JSON 树；历史 `format=constructor` 的 content 为声明式表达式字符串，根分别为 `FrameworkProfile`、`DeviceContract`、`BindingPlan`。同对象 repair 在主机提供唯一完整可解析候选及其 hash 时可使用 `constructor_edits`，按明确字段路径 replace/add，未编辑值保留；格式和范围由当前 schema 固定。表达式作为数据解析，绝不 eval/exec。检查器拒绝未知名称、跨层构造器、缺少必需参数、未知/重复参数及文档省略号。名称/参数检查不等于值类型、单位或行为验证。

代码产物：`kind=code`，`format=file_edits`，`edits` 中每项为：

```json
{
  "path": "target/example.c",
  "operation": "replace",
  "base_sha256": "当前完整基线文件的 SHA-256",
  "content": "候选完整文件内容"
}
```

`replace/delete` 的 hash 必须匹配当前基线；delete 的内容为 null。create 的 hash 为 null，并须经主机确认文件不存在。路径相对工作区、以 target/ 开头且处于授权编辑范围。同一路径只能有一次编辑。输出不含最终 patch 或 submission manifest；主机在准备副本上再次检查基线、应用修改并生成规范提交。

## 3. 五类调用

### fp_build：从框架证据建立能力契约

必需角色为 `method_specification` 和 `framework_evidence`。`framework_context` 给出当前能力问题与版本/配置范围；可加入已有 FP 与方法内部诊断。输出只覆盖此次能力范围，可逐项构建共享 FP，也可补充任务内候选。

需恢复：API 前提及保证、请求接受/拒绝、完成事件、资源所有权、错误表达能力、计时和数值编码。声明不足时查 provider 实现，不能由 API 名称推断物理保证。器件专用 helper、设备命令和补偿关系不进入共享 FP。FP 的整个来源闭包必须限于框架材料；不能把混入器件信息的摘要标为框架知识。在线补充不自动发布到冻结共享库。

### dbc_recover：在已选策略下恢复设备行为

必需 `method_specification`、`task_context`。S/SD 另需 `source_configuration`、`source_evidence`；D/SD 另需 `document_evidence`。可按需加入相关 `source_fp`、源码事实、框架证据和已有 DBC。源 FP 不是每次必需的完整前置产物；只在解释相关 API 效果时按需获取语义。

输出适用条件与已选策略、类型化数据/完整性/物理计算关系、含初始化与采集关系的组合式 FSM、失败/重入的已知约束及未知范围。以获准源路径为行为证据，用原始器件资料核对合法性和遗漏。D 条件只能从器件资料与必要产品条件形成契约，不能声称保持了一个不可见源驱动的完整行为。

固定策略限制合法路径集合，并不消除 I/O 失败、就绪分支、重入和环境条件。不得因配置已给就强行压成单一路径。数据手册与源行为冲突时明确保留，不静默挑选“合理”值。数值以规范化物理关系为主，不把源有限精度算法再设为一个可任选的规范。

### bp_build：建立目标对应和派生义务

必需 `method_specification`、`task_context`、`dbc`、`target_fp`、`target_support`；可加入已有 BP 和内部诊断。输出父引用、动作/事件/资源/请求/数据的绑定、实现选择、派生义务与缺口。

DBC 和 FP 为只读。BP 引用设备命令、布局、公式与框架保证，避免重复维护。特别处理物理完成与回调关系、时钟量化/回绕、接受前后失败、样本与原生请求、单位/精度转换。无法满足父要求时请求证据或升级上游；不得降低 DBC 来适配现有接口。

### code_generate：在已批准范围内生成原生实现

必需 `method_specification`、`task_context`、`dbc`、`target_fp`、`bp`、`target_support`、`validation_report`。修改已有文件时还须提供完整基线，不能只提供局部片段。可按条件附原始源代码或器件资料，用于核对细节；默认使用相关父契约，避免重复塞入完整仓库。

主机须设置 `host_generation_gate=open` 并说明 `generation_scope`。将其设为 open 的依据应来自方法内部结构、证据、能力及绑定检查，且关键缺口已在当前范围内解决，或由明确的任务假设限定。模型不能自行开启 gate。部分范围的原型代码仍是候选，不能宣称完整驱动已正确。

输出候选文件修改。编译、方法内部契约检查和修复可以迭代；独立评分、隐藏参考与硬件诊断均不进入循环。

### repair：定向修复单一层次

必需 `method_specification`、`candidate`、`internal_diagnostic`，并按对象补足以下输入：

| repair_target | 另需 | 只读边界 |
| --- | --- | --- |
| fp | 框架原始证据 | 冻结共享 FP 不被在线覆盖；只产生候选/任务内补充 |
| dbc | task_context 及当前条件要求的原始资料 | 已选策略、原始证据 |
| bp | task_context、DBC、目标 FP、公开目标支持 | DBC/FP |
| code | task_context、DBC、目标 FP、BP、目标支持、内部检查报告及代码基线 | DBC/FP/BP |

范围缺口应由原对象负责：API 保证不足回到 FP，设备约束恢复错误回到 DBC，映射不可满足回到 BP，具体回调遗漏或算术实现错误修复代码。检测器位置不等于根因位置，主机须结合证据分流。升级意味着调度一次新的上游调用，不允许一个响应同时改多个对象。

## 4. 调用前后主机责任

调用前验证 receipt、材料边界、父版本、预算和工具权限；本包实现其中的本地来源与装配检查。调用后按顺序检查封装、引用、构造式/编辑结构、契约语义及其与实现的关系。语义内核尚未实现的检查必须保留为未实施，不能用结构通过代替。

一次调用失败时保留原始输入、响应和诊断，下一次使用新 call_id，明确候选及父版本。修复产物保留从旧候选与新证据到新版本的来源关系。禁止把 benchmark 分数改写成“内部诊断”再输入模型。
