# FP DBC BP 语言规范 v0.1

本版完整定义当前语言的 108 个构造器及其参数和行为语义，覆盖已约定的 FP/DBC/BP 结构。完整的含义是“本版允许使用的名称都有定义”，不是已经覆盖所有器件、框架或已完成形式证明。新需求通过版本扩展，不让模型凭名称发明原语。

| 目录 | 构造器数 | 主要内容 |
| --- | ---: | --- |
| [公共语义](references/shared-language.md) | 19 | 引用、证据归因、条件、未知、事件身份、表达式、组合控制、FSM、不变量及进展 |
| [FP](references/fp-language.md) | 22 | API 要求和保证、执行上下文、同步/异步完成、错误、资源、alarm、输出编码及生命周期 |
| [DBC](references/dbc-language.md) | 31 | 固定条件与策略、连接、事务/寄存器、物理等待、GPIO、帧/CRC、校准、物理关系、采集提交和失败 |
| [BP](references/bp-language.md) | 36 | 只读父引用、同步/异步/事件绑定、计时量化、buffer、数值与错误、请求、并发及义务派生 |

字段签名与语义在这些目录中共同定义。[机器索引](references/primitive-index.json)由文档签名生成，不独立维护另一份接口定义。模型读取语言说明；主机用索引检查名称、参数和所属层次。各构造器的具体值仍须从当前获准证据恢复，通用语言不包含特定器件/OS 答案。

## 语义边界与检查义务

| 需要表达的语义 | 主要落点 | 不能混淆的检查 |
| --- | --- | --- |
| 已选模式与条件 | DBC FrozenPolicy、条件映射 | 固定策略不等于只有一条运行路径 |
| 设备操作与总线边界 | DBC Transfer、寄存器/读帧原语 | 提交不等于开始，API 返回不等于物理完成 |
| 正常、失败、重入与恢复 | DBC Lifecycle + 公共组合/FSM | 未建模不能默认恢复 ready 或直接拒绝 |
| 计算与校准 | DBC 帧/CalibrationData/PhysicalQuantity | 物理关系唯一；源整数算法不是第二套可选规范 |
| 同次采集与样本提交 | DBC CommitValidatedSample | 逻辑配对不证明线程原子性 |
| 框架保证与适用范围 | FP API/ExecutionModel/ResourceProtocol | 声明、单例使用和所有 provider 的保证有区别 |
| 完成事件与时间锚点 | FP 保证 + BP EventBinding | callback 必须匹配当前事务，物理关系须有证据 |
| tick 与物理时间 | BP BindAlarm/CheckedPhysicalLowerBound | 频率、相位、饱和、回绕和上下界分别核对 |
| 原生类型、单位和精度 | FP OutputEncoding + BP NumericEncoding | 比例、偏移、舍入和算法误差不得凭类型猜测 |
| 原生错误通道 | FP ErrorContract + BP 请求绑定 | 接受前拒绝和接受后失败不能替代 |
| buffer 与并发 | FP 资源协议 + BP 资源/并发绑定 | 返还、最后使用和数据有效性分别成立 |
| 安全与进展 | Invariant、Progress、绑定义务 | 有限成功轨迹不能证明无死锁或最终完成 |

## 装配范围

每次自动附带公共响应解释和公共语义。FP 构建/修复附 FP；DBC 恢复/修复附 DBC 及解释源机制所需的 FP 语言；BP 构建/修复和读取完整 BP 的代码生成附全部三类语言。选入父模型时也携带对应定义。这里传递的是通用方法语义，不是其它 trial 或人工器件实例。

当前原型按语言粒度附带全文，并保留预算硬限制。生产集成可在同一目录中按符号与依赖闭合检索语义片段，但不能只给名字、删掉前提和失败含义。语言库是共享定义，器件实例不需要重复其正文；FSM 展开与图表不独立维护。

## 生产实现状态

v0.1.1 的具体参数和检查范围见 IMPLEMENTATION_PROFILE.md。当前检查返回结构化 semantic_validation，逐项保存满足、违反和未知；不把接口检查当成设备语义正确。候选原生执行、证据蕴含与完整进展证明仍须按实际能力分别报告。

在项目根目录运行 `.venv/bin/python -m method.autodriver.implementation.system.protocol.language_catalog`，或运行 `tests/regression/test_autodriver*.py` 对应的离线检查。生产运行不读取研究目录。
