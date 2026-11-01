"""Coarse source domains; explicit categories override title-based defaults."""
DOMAINS = {'algorithms': '算法与编程', 'ai': 'AI 模型与工具',
           'agents': 'Agent 系统', 'society': '社会与公共议题', 'other': '未分类'}

def category(source):
    explicit = source.get('category')
    if explicit in DOMAINS:
        return explicit
    title = source.get('title', '').casefold()
    for key, words in [('agents', ['agent', '智能体', 'harness']),
                       ('algorithms', ['算法', '贪吃蛇', '数据结构']),
                       ('ai', ['deepseek', 'qwen', 'glm-', 'ai日报', 'ai 日报', '模型', 'qoder']),
                       ('society', ['网友', '国混战', '社会', '政治'])]:
        if any(word in title for word in words):
            return key
    return 'other'
