# MPU6050 v2 原始依据审查

宿主资料，不进入 agent。原始 PS-MPU-6000A-00 Rev3.4 与 RM-MPU-6000A-00 Rev4.2；用户确认有符号 µm/s²。旧41项及参考保留，不以旧合同或NineDof usize类型推导物理单位。

| 检查族 | 原始依据、处理与实现自由 |
| --- | --- |
| MPU6050-ABI | board.patch分配14字节并连接I²C/Alarm后startup；公共NineDof服务注册系统调用并绑定client，用户态应用与实物验收仍需另行完成。HIL sensors.rs155–184、libtock-rs ninedof lib.rs140–143按i32消费，host接受低32位零/符号扩展。不要要求私有常量公开、startup固定返回类型。 |
| MPU6050-CONFIG | I²C0x68、±2g加速度、gyro量程±250dps、divider9、DLPF0、无FSYNC/FIFO/外部主机，仅要求XYZ加速度输出。RM pp11–15及38–42支持这些字段；8k/(9+1)=800Hz，不能误写100Hz。允许内部RC/文档支持的gyro PLL、配置前后唤醒顺序和可选中断状态使能。 |
| MPU6050-NUMERIC | PS p13、RM p29给signed16 big-endian、16384LSB/g。独立Fraction乘标准g=9.80665m/s²，比较精确值±1µm/s²；整数截断、负数向下、最近舍入和f32均实际验证。不是物理校准容差，bit极值仍是合法数字编码。65536种轴编码以Decimal和base256交叉验证。 |
| MPU6050-DATA | RM pp29–31内部/用户寄存器在serial idle更新，burst来自同一时刻。模型在START取snapshot、STOP后更新。允许6字节、带温度/gyro的14字节、独立轴对和STOP分开指针写；不用6字节硬编码read长度。各轴输出只需有实际采到的对应字节对，不额外规定三轴必须同epoch；任意跨字节拼接无数据依据仍拒绝。 |
| MPU6050-READY | RM pp25–28 DATA_RDY可选使能/读取，INT_RD_CLEAR支持任意读清状态。状态被清除不删除数据有效性；固定充分等待可以不用ready轮询。不强制尾部status flush或每次请求产生新epoch。 |
| MPU6050-RESET | 正常起点为供电稳定、寄存器默认sleep；唤醒必要，可选软件reset。模型首样本5/10/20ms、reset2ms是有限数字情景，不声称原文提供统一最大值。reset中未知操作、非支持时钟/保留操作明确标识，不能用模型默认猜测证明协议错误。 |
| MPU6050-PROGRESS | 单请求500ms、startup150ms、稳定输入变化1s、回调观察最多1ms、推进50000事件、进程5s、编译300s属于监督预算，绝对截止。旧150ms等待变体不再因旧100ms任意截止被称错误；有限缓存可用，永久旧值在持续刺激下归not_completed。 |
| MPU6050-CALLBACK | 成功接纳的健康请求需要对应一次有效输出；并发可拒绝/排队，不强制BUSY/OFF、startup幂等、回调重入或全局idle。重复、无请求、拒绝后回调错误；健康不完成为not_completed。 |
| MPU6050-DIAGNOSTIC | 错ID、器件停止更新和总线故障仅记录可达性、请求/回调与剩余状态，不规定唯一恢复顺序/错误码。没有总线操作就不把未触发故障当通过。已取得有效数据不因后来无关错误失效。 |

D/SD提供两份原始厂商文档，不附手工寄存器/公式说明；S/SD保留源驱动和生效源配置。实际目标与必要功能在三种条件一致。模型没有验证机械动态精度、陀螺仪/温度输出、FIFO、中断引脚或完整应用，不声称真机可用性已验证。
