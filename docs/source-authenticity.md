# 冻结源资料真实性披露

源选择及各文件的完整 SHA-256 由每个器件的 `source/<os>/selection.yaml` 固定；源验证凭据绑定这些文件、配置和依赖。下面披露已获准的历史处理副本，不将它们称为逐字节未修改的上游原文。本次 autodriver 实施没有再次修改或重新批准这些副本，也没有重建源验证凭据来接受漂移。

| Zephyr 源选择 | 已批准的历史处理 | 实际选入材料及编译角色 |
| --- | --- | --- |
| [APDS9960 / I²C](../benchmark/apds9960/source/zephyr/selection.yaml) | 条件编译分支中的花括号位置经过机械调整。 | 主源码是 `selected/apds9960.c`；历史私有 `selected/apds9960.h` 仍保留但不编译。实际主头文件为 `third_party/zephyr/include/zephyr/drivers/sensor/apds9960.h`。 |
| [CCS811 / I²C](../benchmark/ccs811/source/zephyr/selection.yaml) | 兼容头文件合并声明并去除重复结构体声明。 | 主源码取自 `third_party/zephyr/drivers/sensor/ams/ccs811/ccs811.c`；编译使用 `selected/ccs811_build/ccs811.h`。带重复声明的历史 `selected/ccs811.h`、公共头文件及已批准的 `third_party/metadata/ccs811_source_repair.patch` 另按各自登记角色保留。 |
| [ISL29035 / I²C](../benchmark/isl29035/source/zephyr/selection.yaml) | 历史私有头文件的 include guard 已移除。 | 主源码取自冻结 Zephyr；主头文件为 `selected/isl29035.h`。 |
| [BME280 / I²C、SPI](../benchmark/bme280/source/zephyr/selection.yaml) | 两个源变体共用的历史私有头文件移除了 include guard。 | 主源码和总线依赖取自冻结 Zephyr；两个变体使用同一份 `selected/bme280.h`，分别保留各自配置、设备树和验证凭据。 |

表中的 `selected/` 均相对于相应器件的 `benchmark/<sensor>/source/zephyr/`。这些是四种器件、五个源变体，在现有两个目标 OS 上对应十道原生任务；任务人口仍为44，未因披露新增或合并任务。

为避免把两个历史 CCS811 头文件混为一份，以下列出这些处理文件及其冻结身份。完整材料集合及其它未处理文件仍以所链接的源选择为准。

| 文件 | SHA-256 |
| --- | --- |
| `benchmark/apds9960/source/zephyr/selected/apds9960.c` | `8b29a6b1f4ea3c99e44eaf5bdf942ebc9ecc5126d4dbdaf9e5219ca672bb5843` |
| `benchmark/apds9960/source/zephyr/selected/apds9960.h`（历史保留、不编译） | `8f0328d521bb591cf571dec1a13689f086832335abba591ae5ecbf94253a0f57` |
| `benchmark/ccs811/source/zephyr/selected/ccs811_build/ccs811.h`（编译） | `a51a25f7a95b309c7b7f90c045d3a7e728c4111ca711163b096cc632ebbb4088` |
| `benchmark/ccs811/source/zephyr/selected/ccs811.h`（历史重复声明） | `f278a27deec384e4f0a7b1bc27544690ee946d807087b521916e98cea9c3e44c` |
| `third_party/metadata/ccs811_source_repair.patch` | `7015b4f37cc35f9ee8895a2a5c4418222a97225c1066a1bfa540d19aec6b29c9` |
| `benchmark/isl29035/source/zephyr/selected/isl29035.h` | `30a6b9326956ce8273395b9edb932b524e80329203678b5ccc2e51e47b58c9b7` |
| `benchmark/bme280/source/zephyr/selected/bme280.h` | `f63d25744ea489ecdc6a08849e800722b268afa725340897b8a95dfc522d8abe` |

冻结版本、第三方版权和来源摘要继续见 `third_party/sources.lock`、各上游许可证及原有 provenance。历史来源位置仅用于追溯，不作为 import、默认输入、软链接或缓存依赖。运行只使用本交付根内获准并固定身份的材料。

BMM150 的目标地址 `0x13` 是用户确认的连接配置；MPU6050 的兼容性修改只在隐藏参考实现中，不能迁入源输入或方法材料。供应商补充 DOC/DOCX、真机诊断、校准经验、参考实现和评分反馈不属于方法输入。此披露文档是主机交付说明，不加入迁移 agent 的上下文或知识库。
