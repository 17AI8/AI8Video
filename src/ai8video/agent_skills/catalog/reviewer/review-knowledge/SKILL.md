---
name: review-knowledge
description: 对知识结构进行独立负向审核，并给出有证据、可执行的返工要求。
version: 2.0.0
license: MIT
kind: policy
source: worldwonderer/drama-skills@adab39c-inspired
---

# 独立审核

- 优先寻找原子性、覆盖、层级、适用范围和独立检索方面的真实缺陷。
- 每个返工意见必须引用具体证据，并限定为被审核 Agent 能完成的动作。
- 审核结论必须限定在实际读取的证据范围内；没有检查成片、音频或画面时，不能声称对应媒体质量已经通过。
- 产物 owner 不得用自己的生成结果冒充独立审核；宿主必须提供真实隔离的审核上下文。
- 没有可执行问题时接受结果，不为显得严格而制造返工。
