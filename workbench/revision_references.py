"""Built-in academic guidance offered in the revision dialog."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RevisionReference:
    label: str
    guidance: str
    source: str
    url: str


REVISION_REFERENCES = {
    "evidence": RevisionReference(
        "论证与证据",
        "核对改动是否改变主张的适用范围、必然性、因果强度或观点归属。"
        "用作者给出的原句、改句和相关上下文指出具体依据。"
        "若合并两位学者的结论，说明各自的前提和中间推理如何支持共同结论。"
        "证据不足时，说明缺少哪一步及作者可以核对的材料。不要补造证据、引文或页码。",
        "PLOS 审稿模板",
        "https://plos.org/wp-content/uploads/2020/02/PLOS-Peer-Review-Template.pdf",
    ),
    "clarity": RevisionReference(
        "概念与表述",
        "在读者首次需要理解关键术语的位置检查定义、指代和承接。"
        "保留学科术语及重要区分，例如区分论证的存在条件与证成顺序。"
        "用具体的句段、需要澄清的概念和一个可回答的问题指出表达障碍。"
        "作者未提供定义或论证时，说明需要澄清的关系，保留原有主张强度。",
        "Academic Research Skills · Academic Paper Reviewer",
        "https://github.com/Imbad0202/academic-research-skills/tree/main/academic-paper-reviewer",
    ),
}
