# LSM6DSO v2 原始依据审查

宿主资料，不进入 agent。原文 ST DS12140 Rev3、AN5192 Rev5，保留原始 ST `lsm6dso_reg.c/.h` 依赖。旧46项与参考保持原样。用户确认三轴输出为有符号 µm/s²，原生 NineDof 的 usize 签名没有给定单位。

| 检查族 | 原始依据、处理与实现自由 |
| --- | --- |
| LSM6DSO-ABI | 固定 board.patch 分配14字节、连接I²C/Alarm并startup；公共 NineDof 服务注册系统调用并绑定client，由用户态请求采样；HIL sensors.rs155–184、用户态 ninedof lib.rs140–143 将值解释为i32。host接受低32位的零/符号扩展，不能以64位usize强制多余编码要求。无需公开BUFFER_SIZE或startup特定返回值。板级构建不代表完整硬件应用。 |
| LSM6DSO-CONFIG | 必要选择±2g、104Hz high-performance、gyro off、LPF2/HP off、不启用sensorhub/FIFO/触发，I²C0x6a。DS pp49–56相关CTRL寄存器；FS编码和XL_FS_MODE组合按原文接受等价±2g。不强制配置顺序，未连接的IRQ极性/SPI位可变。BDU是实现策略，不是用户功能配置。 |
| LSM6DSO-NUMERIC | DS p9 Table2名义0.061mg/LSB、pp65–67 signed16 little-endian。独立Fraction以表格十进制值和标准g=9.80665m/s²计算；不替换为MPU6050的1/16384g。允许精确值±1目标整数单位，f32、整数截断和最近舍入控制均实际测试；不是芯片校准精度。覆盖65536种轴编码独立Decimal核对。 |
| LSM6DSO-DATA | AN p29 §4.4明确BDU仅锁定每轴LSB/MSB，X/Y/Z可属于不同采样时刻；去掉旧整体XYZ同epoch要求。按各轴已采到的完整字节对判定，允许各轴不同epoch、分笔/高字节先读、关闭自增后逐字节，或同步读取不用BDU；不得将未采到的值或跨更新撕裂的字节对当有效值。保持原文每字节时钟模型，不伪造整个burst的全轴锁存。 |
| LSM6DSO-SETTLING | AN pp19–20 Tables11/12：104Hz高性能、LPF2/HP关闭时需丢弃第一样本；可使用充分等待、显式丢弃、DRDY mask。AN p29 §4.3.1说明mask过滤未稳定信号，模型同时用于STATUS就绪；DS pp51–52文字强调pin，当前模型解释需披露而不声称额外实测证明。首样本9616/15000/20000us是有限刺激，第二样本以后valid；不能只用init固定150ms隐藏早请求。 |
| LSM6DSO-RESET | 供电稳定、寄存器处于文档默认值、user bank；reset可省略，默认bank可依赖。reset50µs是有限模拟完成时间，不能声明物理硬最大；轮询或足够等待可用，reset中未知写/BOOT/其他bank未建模功能归model_unsupported。任意初始bank40/80单列诊断，不要求驱动从非默认状态恢复。 |
| LSM6DSO-PROGRESS | 已经采到的有效轴值可缓存/复用；不强制每次新epoch。稳定输入改变给1s监督窗口、单请求500ms、启动150ms、回调后最多1ms、单推进50000事件、进程5s、构建300s。采用绝对截止时间；不把这些限额称传感器硬实时要求。 |
| LSM6DSO-CALLBACK | 接受健康请求后有对应回调；并发可拒绝或排队，不强制BUSY/OFF、幂等startup、callback同步重入或后台idle。重复、拒绝后、无请求回调判错；正常条件不完成为not_completed。 |
| LSM6DSO-DIAGNOSTIC | 错型号、停更、传输故障、任意非默认bank只记录观察/是否触发，不规定唯一错误码、清缓存和重启方法。故障没到达为not_exercised。诊断执行完成不等于恢复能力通过。 |

S/SD完整保留源驱动与ST实际依赖；D/SD同时提供原始datasheet和application note，后者包含settling和BDU原始说明，不提供人工提炼。目标集成和物理需求三条件完全一致。不调用方法IR或参考实现充当oracle。当前软件检查不含完整NineDof用户态采样应用或实物精度验证。
