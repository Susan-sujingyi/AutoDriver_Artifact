# AutoDriver

当前方案从准备后的公共源代码、配置、原始器件 PDF 与目标 OS 接口恢复可读 FP、DBC、BP，然后生成候选驱动，通过有界公开编译和有限内部原生检查进行修复。

生产入口为 `adapter.py` → `implementation/system/document_pipeline.py` 与 `document_loop.py`。`implementation/system/` 原样保留提取时的生产实现、契约内核、协议模板及参考语法；不包含旧版 `zephyr2tock/` 或生成知识库。

本分发对每个 trial 重新恢复框架资料，不读取旧开发过程或共享 FP 记录。可读文档为首要产物，派生执行视图只支持已实现的有限检查；缺失覆盖保留 unknown/not_checked。方法检查不能访问 benchmark 私有评分、参考或评分反馈。

`method.yaml` 的 `available` 表示本分发提供调用入口，不等于历史发布资格、全驱动正确或模型性能验收。运行、依赖和预算见[根目录说明](../../README.md)，算法入口见[方法说明](../../docs/METHOD.md)。
