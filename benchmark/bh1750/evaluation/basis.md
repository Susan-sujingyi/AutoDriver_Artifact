# BH1750FVI v2 判分依据

仅供宿主，不进入精简输入。ROHM No.11046EDT01，2011.11 Rev.D 原PDF/text及实际Tock AmbientLight/I²C/Alarm/板级调用是依据。保留所选one-time high-resolution、MTreg69、I²C0x23，不套用ISL29035的寄存器或ready位。

| 检查族 | 原始依据与处置 |
| --- | --- |
| BH-ABI | component实际分配2字节、调用new与startup，HIL回调usize lux。编译检查真实类型，不另要求公开BUFFER_SIZE或startup返回Result。 |
| BH-CONFIG | PDF pp4–5命令/状态机，p11灵敏度选择；目标选择one-time H-resolution、MTreg69。初始化Power On可省略；reset可省略；MTreg默认69，也接受先高后低或先低后高的显式设置。不是强制参考命令序列。 |
| BH-NUMERIC | pp7、10、11规定高字节在前、raw/1.2，MTreg69因子为1；HIL整数lux。Fraction独立oracle并以十进制式穷举65536码交叉核验。允许邻近整数舍入及1LSB有限计算误差，不将数字尺度误差与传感器0.96–1.44物理误差混淆。 |
| BH-TIME | p2 tHR典型120ms、最大180ms，典型不是最小值。60/120/180ms作为数字刺激；候选等180ms或更久均可，只等120ms不能保证首次有效值。1s监督窗口与器件180ms最大完成时间分开；300ms等候不再按旧250ms任意限额判错。 |
| BH-DATA | p7 one-time完成后自动power down，更新需要再发命令。已完成输出保留，不要求每个HIL请求都触发新conversion；缓存和预先转换可用，用稳定变化后有界进展检出永久旧值。reset返回0不等于完成了一次黑暗测量。 |
| BH-I2C | p10每opcode必须STOP、不能一事务多命令；读数无寄存器前缀。允许丢弃早读、单字节的高位探读；完整成功数值须来自实际获取的两字节结果。额外读过两字节、转换中修改MTreg或重叠触发没有充分模型支持，报model_unsupported。p5明确不接受powerdown中的reset和未列opcode，保留对应协议检查。 |
| BH-CALLBACK | 实际AmbientLightClient只有scalar结果，无异步错误码。成功请求/通道/回调对应；拒绝或排队均可，不强制BUSY/OFF、同步回调重入或全局idle。 |
| BH-PROGRESS | 1s初始化/请求/稳定输入观察、50000事件及宿主时限均为监督政策。未交付结果为not_completed；不伪称厂家时序违例。 |
| BH-FAULT | 总线错误注入的到达、返回、挂起数与输出单列诊断；实际消费者不规定唯一恢复方式。旧“第一次拒绝必须立刻返回Err”“故障后必须立即恢复”不再强加给合法重试实现。重复startup也仅诊断。 |

S/SD提供原源工程与生效宏；D/SD提供原厂文档；所有条件提供同一配置、集成目标与真实HIL。没有额外ID、ready、公式、驱动状态机或测试政策提示。VCC/DVI上电复位已完成是模拟初始状态，不能据此证明实物接线。原图中的供电波形不由驱动源码控制。

用例分母、逐项旧新对应和来源哈希在basis/catalog中冻结；所有范围外情况单列。合成控制仅测试评分器；它们不是八题方法实验。

稳定输入进度监督进一步明确为 2000000 µs，与单请求完成窗口分开。绝对截止时间传入每次采样，轮询与回调后观察均裁剪到剩余时间；内层请求不得隐式延长外层窗口。该限额用于有界复测，不是传感器物理时限。旧草案与检查结果保留，冻结时以 profile.json 和当前 harness 为准。
