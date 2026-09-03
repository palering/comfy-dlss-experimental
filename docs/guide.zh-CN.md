# 文档维护规范

[English](guide.en.md) · 简体中文

Audience: public

目标：让后续维护者不依赖聊天记忆就能安全继续工作，避免泄漏私有信息。

## 可见性与写入边界

当前采用 `hybrid`：`docs/` 为公开文档，`.agent-docs/` 为私有工作记忆；只有私有仓库才将过程记录放 `docs/process/`。可见性未知时按公开仓库处理。

默认只在当前工作区写入。未经用户批准具体用途和路径，不写 /tmp、/private/tmp、~/.codex、~/.local、主目录或同级工作区。工具权限不等于用户授权。验证素材放工作区内被忽略的目录，例如 `tmp/agent-docs-tests/`；全局技能同步/安装需先说明来源与目的地并获准。

## 受众标签

实体文档顶部标注 `Audience: public`、`Audience: private-agent` 或 `Audience: internal`。公开/hybrid 仓库的 docs 只接受 public 内容。

## 更新位置

| 内容 | 位置 |
| --- | --- |
| 用户行为、稳定要求、公共架构 | docs/ |
| 当前交接、风险、最新验证 | .agent-docs/process.md 与 .agent-docs/process/ |
| 已接受的私有方向、优先级、策略 | .agent-docs/process/decisions.md |
| 探索、权衡、弃选方案、原始研究 | .agent-docs/process/discussions.md 或 .agent-docs/research/ |
| 可公开的外部参考 | docs/reference/ |

## 公开安全规则

除非所有者明确要求，不公开凭据、token、本机路径、私有 URL、客户数据；未发布路线、定价/业务策略；原始讨论、草稿、失败尝试和 prompt；安全弱点、利用路径、待修复漏洞；私有竞争研究或分发权不清的材料。

## 双语维护

实体文档以 `.en.md` / `.zh-CN.md` 配对，同一次修改同步维护。正文跳转留在当前语言，只有顶部语言切换跨语言。无后缀 README/guide 仅作导航，不维护第三份正文。保留机器标识及权威法律声明原文。运行 `tests/test_documentation.py` 检查配对与本地链接。

## 交接检查

实质修改后更新当前状态文档，只将可公开结论移入 docs，保持 AGENTS 简短；可行时运行 agent-docs check。
