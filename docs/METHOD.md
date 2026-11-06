# 方法与执行边界

四个阶段均使用本次 trial 冻结的公开材料和累计预算：

1. **FP**：选择源、目标框架原文，恢复 API 参数、前置条件、建立动作、责任角色、回调和资源协议。入口为 `framework.py`、`framework_wiring.py`、`evidence.py` 和 `document_pipeline.py`。
2. **DBC**：使用源驱动、有效配置、Tree-sitter/Clang 事实与原始 PDF 恢复器件行为。材料身份、引用行段和父版本受约束，不能加入评分信息。入口为 `source_analysis.py`、`tools.py`、`document_calls.py`。
3. **BP**：将设备操作绑定到目标框架调用、状态迁移及失败清理，保留未证明的前提和覆盖缺口。可读 FP/DBC/BP 是首要文档；`contract/` 与 `document_view_compiler.py` 提供诊断及可选执行视图。
4. **生成与修复**：候选由主机绑定允许路径和基线，在独立副本上应用、公开编译并进行可用的有限内部检查。入口为 `document_code.py`、`document_loop.py`，主机入口为 `tests/common/autodriver_tools.py`。

每题最多四次累计代码修复、五次候选检查、五次公开完整构建；格式重试和补证仍计入相应请求及修复预算。最终只提交最后实际应用且被当前方法结果交付的候选，之后独立评分。

方法内部可以执行从契约派生的有限 Tock/NuttX I²C、SPI、标量轨迹，或有公开 ABI 支持的框架错误路径探测。GPIO、向量和部分 NuttX SPI 的设备级原生覆盖仍有限。无法降低的契约、不具备的 provider 保证、完整时限/活性、通用数值正确性及原文蕴含均保留 unknown，不因解析或编译成功而消失。

独立 benchmark 位于 `benchmark/*/evaluation/`，主机评分入口为 `tests/common/pipeline.py`。它在最终候选冻结后使用隐藏参考模型和轨迹评分，不向生成/修复过程回传结果。

本独立分发保留全方法及四种机制消融；每个 trial 从原始材料重建框架契约，费用包含该 trial 的全部在线调用，不复用任何历史知识构建或对照实验。
