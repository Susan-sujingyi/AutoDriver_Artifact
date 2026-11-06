# AutoDriver Artifact

## 环境与离线安装

需要 Linux x86_64、Python 3.13、Landlock ABI ≥ 3、`libseccomp.so.2`。系统工具包括 `git`、`make`、GCC/G++、`arm-none-eabi-gcc`/binutils、`cmake`、`ninja`、`dtc`、`pdftotext`、`clang-14` 和 `rustup`。系统工具须预先安装，仓库随附 Python wheels、OS 源码和构建所需的 Cargo vendor 依赖。

Rust 工具链需要预先安装；以下命令需要访问 Rust 分发服务：

```sh
rustup toolchain install nightly-2024-01-01 --profile minimal --component rust-src
rustup target add thumbv7em-none-eabi --toolchain nightly-2024-01-01
rustup toolchain install 1.88.0 --profile minimal
rustup target add thumbv7em-none-eabi --toolchain 1.88.0
```

在本仓库根目录执行：

```sh
python3.13 tests/common/setup.py
export PYTHONDONTWRITEBYTECODE=1
.venv/bin/python -m tests.common.pipeline list
.venv/bin/python scripts/check.py
bash scripts/test.sh
```

进一步检查公共 SD 输入准备和隔离交接，以及三个代表任务的真实原生构建、独立软件评分和固件打包：

```sh
.venv/bin/python scripts/check.py --inputs representative --native
# 全部 44 题的公共 SD 输入交接，耗时更长：
.venv/bin/python scripts/check.py --inputs all
```

这些检查不调用模型、不连接硬件；参考实现只在主机侧验证 benchmark。编译通过、有限内部检查或软件评分通过均不等于真机正确。

## 运行 AutoDriver

克隆仓库后，从模板新建它并填写自己的服务信息：

```sh
cp .env.example .env
chmod 600 .env
```

必须填写 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`。模型服务需要兼容 Responses API、流式返回、reasoning 和输出 token 上限。密钥只由主机网关读取，不提供给方法进程。

同时配置 `dependencies/autodl-pricing.json` 中的 provider/model、实际价格、最大计费输入及输出上限；核实后填写 `source`、`checked_at`，将 `authority` 改为 `user_provided`。原有数字仅作格式示例，模板状态会拒绝付费运行。该文件名沿用执行层接口，不要求使用某一家服务。将实验 YAML 中的 `model` 填为同一模型。

修改公开配置或代码后，显式生成新的内容身份；该操作会核验既有源验证凭据，不会重编源 OS 或生成模型性能证书：

```sh
.venv/bin/python scripts/freeze.py
# 只展开计划，零模型调用：
.venv/bin/python -m tests.common.experiment --config examples/smoke.yaml
# 真正执行单题，会调用模型并计费：
.venv/bin/python -m tests.common.experiment \
  --config examples/smoke.yaml --execute --batch smoke-001
# 全题库：
.venv/bin/python -m tests.common.experiment \
  --config examples/all44.yaml --execute --batch all44-001
```

新方法的 live 条件为 **SD**；S/D 只保留 benchmark 输入准备和离线交接接口。默认每题最多 64 次请求、1800 秒、20 元，四次累计代码修复、五次候选检查和五次公开完整构建。全题库示例包含 44 次独立 trial，总预算上限 880 元；共享预处理历史成本不参与此独立运行方式。

`variant` 支持 `full`、`without_source_framework_lifting`、`without_dbc_evidence_refinement`、`without_binding_plan`、`compile_only_repair`。这些是同一新方法的机制消融。

新运行的结果写入 `result/autodriver/<experiment>/<batch>/`，包括 `summary.csv`、请求与费用、候选 patch 和独立评分；主机控制信息在 `result/acceptance/<batch>/`。`run/` 是临时工作区，两个目录均被 Git 忽略。原样恢复已完成/可恢复批次可追加 `--resume`；未完成进程或归档会拒绝自动覆盖。

## 目录与材料边界

| 路径 | 内容 |
| --- | --- |
| `benchmark/` | 任务、原始源选择、数据手册、公共接口、主机隐藏参考与独立评分器 |
| `method/autodriver/implementation/system/` | FP/DBC/BP、证据检索、生成、原生内部检查与修复算法 |
| `method/common/` | 公共输入接口及受限网关客户端 |
| `tests/common/` | 准备、隔离、模型网关、预算、候选构建、评分与新结果归档 |
| `tests/regression/` | 离线回归及合成夹具 |
| `third_party/` | 固定 OS 源码、许可证、离线 wheels 和构建依赖 |
| `manifest.json` | 当前精简分发的完整性身份 |

方法只能访问准备后的公共材料和获准工具，不能读取隐藏参考、独立评分器、厂商补充材料或评分反馈。benchmark 内最小源验证 receipt 是固定输入的完整性元数据；其历史日志指针已移除，不携带原实验或验收结果。旧发布资格不会被本仓库继承。

方法说明见 [docs/METHOD.md](docs/METHOD.md)，提取范围与改动见 [docs/EXTRACTION.md](docs/EXTRACTION.md)，已处理源副本披露见 [docs/source-authenticity.md](docs/source-authenticity.md)。保留 [第三方版权及许可证说明](THIRD_PARTY_NOTICES.md)。
