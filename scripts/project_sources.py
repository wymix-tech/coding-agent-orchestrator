"""Host-observed project choice evidence; CLI source labels are not authority."""
import re

from scripts.project_context import ROOT, digest, read
from scripts.state_store import atomic_json

ALIASES = {'springboot': ('springboot', 'spring boot'), 'junit5': ('junit5', 'junit 5', 'jupiter'),
           'typescript': ('typescript', 'ts'), 'javascript': ('javascript', 'js'),
           'notapplicable': ('not applicable', '不适用', '无框架', '无测试', '纯文档')}


def compact(value):
    return re.sub(r'[\s_.-]+', '', str(value).casefold())


def mentions(text, value):
    if value == 'not_applicable':
        return any(word in text.casefold() for word in ALIASES['notapplicable'])
    values = ALIASES.get(compact(value), (value,))
    for item in values:
        pieces = re.split(r'[\s_.-]+', item)
        pattern = r'(?<![a-z0-9])' + r'[\s_.-]*'.join(re.escape(piece) for piece in pieces) + r'(?![a-z0-9])'
        for match in re.finditer(pattern, text, flags=re.I):
            prefix = text[max(0, match.start() - 30):match.start()].casefold()
            if re.search(r"(?:不要|不用|不使用|而非|不是|not|without|don't use|do not use)\s*$", prefix):
                continue
            suffix = text[match.end():match.end() + 8].casefold()
            if re.match(r'\s*(?:or\b|还是|或者|或)', suffix):
                continue
            if re.search(r'(?:or\b|还是|或者|或)\s*$', prefix):
                continue
            return True
    return False


def observe_prompt(repo, prompt, host, session_id):
    if not isinstance(prompt, str) or not prompt.strip() or not session_id:
        return
    from scripts.runtime_logs import redact
    if len(prompt.encode('utf-8')) > 65536:
        return
    prompt = redact(prompt)
    identity = 'user-' + digest({'prompt': prompt, 'host': host, 'session_id': session_id})
    path = repo / ROOT / 'evidence' / (identity + '.json')
    if not path.exists():
        atomic_json(path, {'id': identity, 'source': 'host-user-input', 'quote': prompt,
                           'host': host, 'session_id': session_id})
    return identity


def user_evidence(repo, reference, fields, delegated=False):
    if not reference or not re.fullmatch(r'user-[a-f0-9]{64}', reference):
        raise ValueError('PROJECT_SOURCE_REQUIRED: use evidence IDs from cao project inspect; restore explicit choices through a host-observed user message')
    evidence = read(repo / ROOT / 'evidence' / (reference + '.json'))
    if not evidence or evidence.get('id') != reference or evidence.get('source') != 'host-user-input':
        raise ValueError('PROJECT_SOURCE_INVALID')
    quote = evidence['quote']
    if delegated:
        # Explicit field names bound delegation; generic continue/provider answers never qualify.
        if not any(word in quote.casefold() for word in ('you choose', '你来选', '你决定', '由你选择')):
            raise ValueError('PROJECT_DELEGATION_REQUIRED')
        for key in fields:
            terms = {'language': ('language', '语言'), 'framework': ('framework', '框架'),
                     'build_tool': ('build', '构建'), 'test_framework': ('test', '测试'),
                     'storage': ('storage', '数据库', '存储'), 'delivery': ('delivery', '交付')}
            if not any(term in quote.casefold() for term in (*terms.get(key, (key,)), '技术栈', 'technology stack')):
                raise ValueError('PROJECT_DELEGATION_SCOPE_INVALID: ' + key)
    elif any(not mentions(quote, value) for value in fields.values()):
        raise ValueError('PROJECT_SOURCE_FIELD_NOT_COVERED: choices must occur in actual user input')
    return {**evidence, 'covered_fields': sorted(fields), 'source': 'user-delegated' if delegated else 'user-specified'}


def observe_question(repo, raw, event, host, session_id):
    """Reuse the host's request-bound question reply extraction (including Pi)."""
    from scripts.native_route import bind_approval_question, observed_replies
    if not session_id or (raw.get('tool_name') or raw.get('toolName')) != 'ask_user_question':
        return
    path = repo / ROOT / 'questions.json'
    record = read(path, {'id': 'project-setup', 'work_item_id': 'project-setup', 'contexts': [], 'questions': [], 'inputs': {}})
    context = {'host': host, 'session_id': session_id}
    if context not in record['contexts']:
        record['contexts'].append(context)
    call_id = raw.get('toolCallId') or raw.get('tool_call_id') or raw.get('tool_use_id')
    if event == 'pre_tool':
        if bind_approval_question(record, 'project-setup', raw, host, session_id):
            inputs = raw.get('tool_input') or raw.get('input') or {}
            if len(str(inputs)) < 65536:
                record['inputs'][str(call_id)] = inputs
            atomic_json(path, record)
        return
    replies = observed_replies(record, 'project-setup', raw, event, host, session_id)
    if not replies:
        return
    options = []
    def collect(value):
        if isinstance(value, dict):
            if isinstance(value.get('label'), str):
                options.append(value)
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
    collect(record['inputs'].get(str(call_id), {}))
    for reply in replies:
        selected = [option for option in options if option['label'] == reply]
        quote = reply
        if len(selected) == 1:
            quote += ' ' + str(selected[0].get('description') or '')
        identity = observe_prompt(repo, quote, host, session_id)
        if identity:
            evidence_path = repo / ROOT / 'evidence' / (identity + '.json')
            evidence = read(evidence_path)
            evidence.update(tool_call_id=call_id, selected_reply=reply)
            atomic_json(evidence_path, evidence)
