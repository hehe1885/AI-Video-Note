"""Note styles share generation pipelines but keep distinct editorial instructions."""
NOTE_STYLES = {
    'learning': {
        'label': '学习讲义', 'base': 'faithful',
        'guide': '以文字解释为主，不写成只有术语列表、复习题或图片的提纲。每个核心概念先解释含义、条件及原理，再结合材料中的案例展开；图片不能替代解释，材料没有案例时不编造。按学习讲义组织：学习目标、核心概念、原理解释、材料中的例子、容易混淆的点和复习问题。用清晰语言解释概念之间的联系，保留重要细节和时间点。复习题及参考答案必须基于材料，缺少的信息注明未提及；不擅自扩展外部知识。',
    },
    'tutorial': {
        'label': '步骤教程', 'base': 'faithful',
        'guide': '以能照着执行的文字说明为主，不能只列步骤标题或依赖截图。每个主要步骤说明做什么、如何操作、材料明确的参数及如何确认结果；缺少的信息注明未提及，不能为了增加字数编造。按步骤教程组织：目的、材料明确的前置条件、按顺序的操作步骤、实际出现的参数或代码、结果检查、注意事项与常见问题。保留关键操作时间点和示例。没有提到的命令、工具版本、参数和排错办法不得编造；若材料不是操作演示，按其中实际方法整理，不凑虚构步骤。',
    },
    'cards': {
        'label': '知识卡片', 'base': 'faithful',
        'guide': '拆分为简短且独立的知识卡片，每张使用一个明确的小标题，涵盖一个概念、结论或方法；包含简要解释、材料中的例子或依据，以及有用的来源时间点。减少重复，保留不同知识点及其条件，不因卡片短就丢失视频后半段内容。卡片数量随内容决定，不设置固定张数。',
    },
    'outline': {
        'label': '思维大纲', 'base': 'concise',
        'guide': '输出便于转为思维导图的 层级大纲：一个中心主题，下设主要分支和子分支，用缩进列表表达概念的包含、因果、对比或步骤关系。节点文字简洁，重要例子和时间点放在对应节点下。覆盖视频的主要主题和结论，不生成 Mermaid、不把整篇放入代码围栏。',
    },
    'meeting': {
        'label': '会议纪要', 'base': 'concise',
        'guide': '按会议纪要组织：讨论议题、主要观点或分歧、明确结论与决定、待办事项、未解决问题。待办可用表格记录行动项、负责人、截止时间；负责人、日期、参会人和决定只有材料明确时才填写，否则注明未提及，不替参与者作决定。普通视频也只能整理实际讨论内容，不假装发生过会议。',
    },
}


# Translations share the same style identifiers as the Chinese guides.
STYLE_GUIDES_EN = {'faithful': 'Organize standalone notes by topic and logical progression, covering all major topics through the end. Explain core concepts, main claims and evidence. Retain useful source examples, key steps, parameters, conditions, and important causal, comparative or sequential relationships. Merge verbal repetition and irrelevant setup; avoid verbatim transcription or headings and conclusions without explanations. Let the material determine structure and the selected detail level determine depth. Do not add outside knowledge or evaluation.',
 'concise': 'Extract the central problem, main claims, key evidence and conclusions across all major topics, including later sections. Prioritize steps, parameters, conditions and limitations needed for understanding or practical use. Retain only representative examples that clarify conclusions. Merge related information and remove repetition, setup and secondary details. Use short paragraphs or clear bullets, not a terminology list. Follow the selected detail level without changing meaning or turning conditional judgments into absolute claims.',
 'detailed': 'Organize all material and clearly distinguish the source from any requested AI commentary.',
 'obsidian': 'Organize faithful notes suitable for Obsidian.',
 'learning': 'Write explanatory study notes, not a terminology list, quiz or image-only outline. Explain '
             'each core concept, its conditions and principles before developing examples from the material. '
             'Images cannot replace explanations. Organize learning goals, concepts, principles, source '
             'examples, common confusions and review questions. Preserve important details and timestamps. '
             'Questions and answers must be grounded in the material; identify missing information and do '
             'not invent examples or external knowledge.',
 'tutorial': 'Write instructions a reader can follow. For each major step explain what to do, how to do it, '
             'the stated parameters and how to verify the result. Organize purpose, stated prerequisites, '
             'ordered steps, actual parameters or code, checks, precautions and common issues. Preserve key '
             'timestamps and examples. Do not invent commands, versions, parameters or troubleshooting '
             'steps; if the source is not a demonstration, describe its actual methods.',
 'cards': 'Split the material into short, independent knowledge cards with clear subheadings. Each covers '
          'one concept, conclusion or method, a brief explanation, source examples or evidence, and useful '
          'timestamps. Reduce repetition without losing distinct points, conditions or later topics. Let '
          'content determine the number of cards.',
 'outline': 'Write a hierarchical outline suitable for a mind map: one central topic with branches and '
            'subbranches. Use indented lists for inclusion, cause and effect, comparisons and steps. Keep '
            'nodes concise, placing important examples and timestamps under the relevant node. Cover major '
            'topics and conclusions. Do not generate Mermaid or wrap the document in a code fence.',
 'meeting': 'Organize minutes into topics, viewpoints or disagreements, explicit decisions, action items and '
            'unresolved questions. A table may list actions, owners and deadlines. Include owners, dates, '
            'attendees and decisions only when stated; otherwise mark them as unspecified. Do not decide on '
            "participants' behalf or pretend an ordinary video was a meeting."}

def style_guide(style, language="zh-CN"):
    if language == "en":
        return STYLE_GUIDES_EN.get(style, STYLE_GUIDES_EN["faithful"])
    return NOTE_STYLES.get(style, {}).get("guide") or {
        "faithful": "按主题和内容逻辑整理成可独立阅读的完整笔记，覆盖视频前后所有主要话题。说明核心概念、主要观点及其依据，保留有助于理解的具体例子、关键步骤、参数和适用条件，交代重要的因果、对比或递进关系。合并口头重复和无关铺垫，不写成逐句转录，也不只列标题或结论。结构随材料自然展开，展开程度遵循所选文字详略；忠实于材料，不补充外部知识或评价。",
        "concise": "围绕视频的核心问题提炼主要观点、关键依据和最终结论，覆盖各个主要主题，不只概括开头。优先保留影响理解或实际使用的步骤、参数、条件与限制；例子仅保留最有代表性且有助于解释结论的部分。合并同类信息，删去重复、铺垫和次要枝节，用简短段落或清晰条目组织，避免只堆术语。展开程度遵循所选文字详略，不改变原意，不把有条件的判断写成绝对结论。",
        "detailed": "完整整理材料，区分原文与用户明确请求的 AI 点评。",
        "obsidian": "忠实整理材料，组织为适合 Obsidian 阅读的笔记。",
    }.get(style, "忠实整理材料。")
