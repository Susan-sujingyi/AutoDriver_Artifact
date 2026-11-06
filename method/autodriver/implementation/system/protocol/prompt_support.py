"""Public explanations for a model that has no prior project conversation."""

ROLE_DESCRIPTIONS = {
    "method_specification": "通用输出格式说明；不是当前器件的事实证据",
    "task_context": "本次公开任务要求、器件连接、已选策略及范围",
    "framework_context": "正在分析的操作系统、版本、配置和接口范围",
    "framework_evidence": "操作系统接口的原始声明、文档或实现",
    "existing_fp": "待补充或修订的操作系统接口说明候选",
    "source_configuration": "源驱动已经选定的配置；不允许重新优化其设备策略",
    "source_evidence": "需要分析的源驱动代码",
    "source_facts": "工具从获准源代码和配置中提取的事实",
    "source_fp": "源操作系统接口说明：解释源驱动调用的前提和效果",
    "target_fp": "目标操作系统接口说明：可用接口、事件、资源、单位及限制",
    "document_evidence": "原始器件资料或其可追踪的机械文本提取",
    "existing_dbc": "待修订的设备行为模型候选",
    "dbc": "给定条件和策略下必须保持的设备交互与采集行为模型",
    "existing_bp": "待修订的目标实现对应方案",
    "bp": "设备行为与目标操作系统机制之间的实现对应方案",
    "target_support": "公开目标集成要求，例如入口、注册、容量和允许修改的位置",
    "target_code": "当前目标文件内容；修改时必须以此为基线",
    "validation_report": "当前方法内部检查器的发现；只支持其明确检查的范围",
    "internal_diagnostic": "当前候选的方法内部诊断；不是独立 benchmark 评分",
    "candidate": "本次指定要修订的候选对象",
}

ARTIFACT_DESCRIPTIONS = {
    "fp": ("操作系统接口说明", "FrameworkProfile"),
    "dbc": ("设备交互与采集行为模型", "DeviceContract"),
    "bp": ("目标实现对应方案", "BindingPlan"),
    "code": ("目标驱动候选文件修改", None),
}


def task_brief(request, kind):
    if kind == "fp":
        policy = "本次只分析获准的操作系统框架材料，不恢复某个传感器的命令或协议。"
    else:
        policy = {
            "S": "本次可使用已提供的源驱动、源配置及获准框架/产品资料；不可使用器件手册或它的派生信息。",
            "D": "本次可使用已提供的器件原始资料、产品要求和目标框架资料；不可读取源驱动、源配置、源框架或其派生信息。不能声称保持了不可见的源行为。",
            "SD": "本次可使用已提供的源驱动、冻结配置、原始器件资料及获准框架/产品资料；仍须保留证据冲突和适用条件。",
        }[request["condition"]]
    return {"本次具体目标": request["goal"],
            "应交付的对象": ARTIFACT_DESCRIPTIONS[kind][0],
            "材料使用范围": policy,
            "前提": "当前消息包含本次指令、实际输入和格式定义。不要假设存在先前对话、未提供文件或未声明工具。"}


def output_template(request, kind):
    artifact = {"kind": kind, "format": "constructor",
                "content": "在此写入一个完整的 " + str(ARTIFACT_DESCRIPTIONS[kind][1]) + " 声明式表达式字符串"}
    if kind == "code":
        artifact = {"kind": "code", "format": "file_edits", "edits": []}
    elif request.get('contract_transport')=='constructor_tree':
        artifact={'kind':kind,'format':'constructor_tree','content':{'$constructor':ARTIFACT_DESCRIPTIONS[kind][1]}}
    return {"schema": "autodriver.llm-response/v0.1", "call_id": request["call_id"],
            "stage": request["stage"], "outcome": "candidate", "artifact": artifact,
            "evidence_links": [], "unresolved": [], "context_requests": [], "change_summary": []}


OUTPUT_INSTRUCTIONS = """output_template 是待填写的响应模板，不是已有答案。
schema、call_id、stage、artifact.kind、artifact.format 已为本次调用填好，保留这些固定值。
契约任务按 output_template/response_schema 填写：constructor_tree 的 content 是含 $constructor 和命名参数的完整 JSON 对象；constructor 的 content 才是完整表达式字符串。若当前提供 contract_repair_base，优先用 constructor_edits，仅编辑这份固定候选的必要字段，允许的完整载体以本次schema为准。代码任务用 file_edits，在 edits 中填累计完整修改。
evidence_links 每项只含 claim（具体断言）、basis（简短依据）、citations（当前 context 实际 citation_id 的数组）。不要手写 material_id/start_line/end_line，也不要混用句柄和行号字段。主机把句柄还原为该片段的原材料、hash 和行段；这不证明断言受原文支持。至少引用关键断言的实际依据，不能只引用格式说明。
Constraint.evidence 填本响应 evidence_links 中完全相同的 claim 文本/标签；不是 material_id，也不是 citation_id。证据链接本身的 citations 才填写片段句柄。FP 引用框架原文，DBC 引用当前源及手册原文，BP 分别引用只读 DBC、FP 与公开集成父材料。不要把未定义的 API 场景或不适用资源写成需要恢复的保证；可确认没有该参数/资源/恢复策略时，在字段语义允许的范围内忠实表达 None、空集或不支持，并引用依据，不能用 None 抹去真实缺口。
unresolved 的每项含 subject、reason、impact，impact 为 required/optional/unknown。
context_requests 最多八项，每项含 question、scope、query、reason；scope 从 allowed_context_request_scopes 选。普通 query 为检索词；internal_tools 的 query 使用实施说明规定的有限 tool/arguments JSON，主机只调用实际列出的 executor，不执行任意命令或路径。
change_summary 填简短产物摘要。没有缺口或请求时对应数组可为空。
证据不足以形成候选时，可把 outcome 改为 needs_evidence 并把 artifact 改为 null，填写补充请求；本次无法继续则用 blocked 并说明未决原因。
只有阶段要求的模型或修改是本次产物，不要把这些格式说明复制成答案。"""
