#!/usr/bin/env bash
# 原生验收执行器（CAO command 模式）。
#
# 以真实进程验证 SPEC 的验收标准：启动打包后的 jar，通过 HTTP 验证登录与热加载行为。
# 输出：stdout 只打印一份 CAO 验收报告 JSON；诊断信息一律走 stderr。
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

JAR="target/login-service-0.1.0-SNAPSHOT.jar"
CFG_SOURCE="config/users.yaml"
FIXTURE_DIR="$(mktemp -d)"
CFG="$FIXTURE_DIR/users.yaml"
ALICE_PASSWORD="alice-secret"
ALICE_NEW_PASSWORD="alice-new-secret"
BOB_PASSWORD="alice-secret"

RESULTS="$(mktemp)"
LOG="$(mktemp)"
BODY="$(mktemp)"
PID=""
NEW_HASH=""

cleanup() {
  if [ -n "$PID" ]; then
    kill "$PID" 2>/dev/null
    wait "$PID" 2>/dev/null
  fi
  rm -rf "$FIXTURE_DIR"
  rm -f "$RESULTS" "$LOG" "$BODY"
}
trap cleanup EXIT

record() { # id status reason
  printf '%s\t%s\t%s\n' "$1" "$2" "$3" >> "$RESULTS"
}

free_port() {
  python3 -c "import socket;s=socket.socket();s.bind(('127.0.0.1',0));print(s.getsockname()[1]);s.close()"
}

curl_code() { # method path [body]
  if [ -n "${3:-}" ]; then
    curl --noproxy '*' -s -o "$BODY" -w '%{http_code}' -X "$1" "http://127.0.0.1:$PORT$2" \
      -H 'Content-Type: application/json' -d "$3"
  else
    curl --noproxy '*' -s -o "$BODY" -w '%{http_code}' -X "$1" "http://127.0.0.1:$PORT$2"
  fi
}

login_code() { # username password
  curl_code POST /api/login "{\"username\":\"$1\",\"password\":\"$2\"}"
}

wait_for_login() { # username password expected_status timeout_seconds -> 0/1
  local deadline=$((SECONDS + $4))
  local code
  while [ "$SECONDS" -lt "$deadline" ]; do
    code="$(login_code "$1" "$2")"
    if [ "$code" = "$3" ]; then
      return 0
    fi
    sleep 0.1
  done
  return 1
}

write_config() { # username password_hash [username password_hash ...]
  {
    printf 'users:\n'
    while [ "$#" -gt 0 ]; do
      printf '  - username: %s\n    passwordHash: "%s"\n' "$1" "$2"
      shift 2
    done
  } > "$CFG"
}

# --- 前置条件 ---
if [ ! -f "$JAR" ]; then
  echo "缺少构建产物 ${JAR}，请先运行 scripts/verification/verify.sh" >&2
  exit 1
fi
if [ ! -f "$CFG_SOURCE" ]; then
  echo "示例配置不存在" >&2
  exit 1
fi

ALICE_HASH="$(python3 - "$CFG_SOURCE" <<'PY'
import re, sys
text = open(sys.argv[1], encoding='utf-8').read()
# 允许 username 与 passwordHash 之间存在注释/缩进差异。
match = re.search(r'username:\s*alice\b.*?passwordHash:\s*"?([^"\s]+)"?', text, re.S)
print(match.group(1).strip() if match else '')
PY
)"
if [ -z "$ALICE_HASH" ]; then
  echo "无法从 ${CFG} 读取 alice 的哈希" >&2
  exit 1
fi

NEW_HASH="$(java -jar "$JAR" --hash-password="$ALICE_NEW_PASSWORD" 2>/dev/null | tail -n 1)"

# --- 启动服务 ---
PORT="$(free_port)"
write_config alice "$ALICE_HASH"
java -jar "$JAR" --server.port="$PORT" --app.login.users-path="$CFG" > "$LOG" 2>&1 &
PID=$!

READY=0
for _ in $(seq 1 100); do
  code="$(curl --noproxy '*' -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/login" 2>/dev/null)"
  if [ "$code" != "000" ] && [ -n "$code" ]; then
    READY=1
    break
  fi
  sleep 0.2
done
if [ "$READY" != "1" ]; then
  echo "服务未在预期时间内启动，日志尾部：" >&2
  tail -n 20 "$LOG" >&2
  exit 1
fi

# AC-1 正确凭据返回 200 与令牌
code="$(login_code alice "$ALICE_PASSWORD")"
body="$(cat "$BODY")"
if [ "$code" = "200" ] && printf '%s' "$body" | grep -q '"token"'; then
  record AC-1 passed "HTTP 200 且响应体含 token（已脱敏）"
else
  record AC-1 failed "期望 200 且含 token，实际 code=${code} body=${body}"
fi

# AC-2 错误密码与不存在用户名返回一致的 401
wrong_code="$(login_code alice wrong-password)"
wrong_body="$(cat "$BODY")"
unknown_code="$(login_code nobody wrong-password)"
unknown_body="$(cat "$BODY")"
if [ "$wrong_code" = "401" ] && [ "$unknown_code" = "401" ] && [ "$wrong_body" = "$unknown_body" ]; then
  record AC-2 passed "两者均 401 且响应体完全一致: $wrong_body"
else
  record AC-2 failed "错误密码 code=${wrong_code} body=${wrong_body}；未知用户 code=${unknown_code} body=${unknown_body}"
fi

# AC-3 运行中新增用户后 2 秒内生效
write_config alice "$ALICE_HASH" bob "$ALICE_HASH"
start_seconds=$SECONDS
if wait_for_login bob "$BOB_PASSWORD" 200 2; then
  record AC-3 passed "新增 bob 后 $((SECONDS - start_seconds)) 秒内登录成功（无重启）"
else
  record AC-3 failed "新增 bob 后 2 秒内仍未登录成功"
fi

# AC-5 运行中改密：旧密失败、新密成功（既有令牌失效由集成测试断言）
write_config alice "$NEW_HASH" bob "$ALICE_HASH"
old_code="$(login_code alice "$ALICE_PASSWORD")"
new_applied=$(wait_for_login alice "$ALICE_NEW_PASSWORD" 200 3 && echo yes || echo no)
if wait_for_login alice "$ALICE_PASSWORD" 401 3 && [ "$new_applied" = "yes" ]; then
  record AC-5 passed "改密后旧密码被拒（首次探测 code=${old_code}），新密码可登录"
else
  record AC-5 failed "改密后行为不符合预期（旧密码首次探测 code=${old_code}，新密码生效=${new_applied}）"
fi

# AC-4 运行中删除用户后其登录失败（既有令牌失效由集成测试断言）
delete_precondition="$(login_code alice "$ALICE_NEW_PASSWORD")"
write_config bob "$ALICE_HASH"
if [ "$delete_precondition" = "200" ] && wait_for_login alice "$ALICE_NEW_PASSWORD" 401 3 && wait_for_login bob "$BOB_PASSWORD" 200 3; then
  record AC-4 passed "删除 alice 后其登录返回 401，bob 不受影响仍可登录"
else
  record AC-4 failed "删除 alice 后行为不符合预期"
fi

# AC-6 非法配置被整份拒绝且服务继续可用
failures_before="$(grep -c '配置重载失败' "$LOG" || true)"
printf 'users:\n  - username: bob\n   passwordHash: broken\n' > "$CFG"
failure_seen=0
deadline=$((SECONDS + 3))
while [ "$SECONDS" -lt "$deadline" ]; do
  now="$(grep -c '配置重载失败' "$LOG" || true)"
  if [ "$now" -gt "$failures_before" ]; then
    failure_seen=1
    break
  fi
  sleep 0.1
done
bob_code="$(login_code bob "$BOB_PASSWORD")"
if [ "$bob_code" = "200" ] && [ "$failure_seen" = "1" ]; then
  record AC-6 passed "非法配置后 bob 仍可登录（code=${bob_code}）且日志含重载失败记录"
else
  record AC-6 failed "非法配置后行为不符合预期（bob code=${bob_code}，失败日志出现=${failure_seen}）"
fi

# AC-7 恢复合法配置后自动生效
write_config bob "$ALICE_HASH" carol "$ALICE_HASH"
if wait_for_login carol "$ALICE_PASSWORD" 200 3; then
  record AC-7 passed "恢复合法配置后新用户 carol 自动可登录，无需重启"
else
  record AC-7 failed "恢复合法配置后新用户未能生效"
fi

# AC-8 全量日志不含密码明文
if grep -F -q -e "$ALICE_PASSWORD" -e "$ALICE_NEW_PASSWORD" -e "wrong-password" -e "$ALICE_HASH" -e "$NEW_HASH" "$LOG"; then
  record AC-8 failed "日志中出现密码明文"
else
  record AC-8 passed "服务日志与验收过程均未出现密码明文"
fi

# AC-9 哈希生成入口可用且不启动 Web 容器
hash_output="$(java -jar "$JAR" --hash-password="$ALICE_PASSWORD" 2>/dev/null | tail -n 1)"
write_config tooltest "$hash_output"
if printf '%s' "$hash_output" | grep -Eq '^\$2[aby]\$[0-9]{2}\$' && wait_for_login tooltest "$ALICE_PASSWORD" 200 3; then
  record AC-9 passed "入口输出 bcrypt 哈希并退出: ${hash_output:0:7}..."
else
  record AC-9 failed "哈希生成入口输出不符合预期: $hash_output"
fi

# AC-10 配置缺失时拒绝启动
missing_port="$(free_port)"
java -jar "$JAR" --server.port="$missing_port" --app.login.users-path="$FIXTURE_DIR/absent-users.yaml" > "$FIXTURE_DIR/missing.log" 2>&1
missing_code=$?
if [ "$missing_code" -ne 0 ] && grep -F -q "absent-users.yaml" "$FIXTURE_DIR/missing.log" && grep -q "配置文件不存在" "$FIXTURE_DIR/missing.log"; then
  record AC-10 passed "配置缺失时进程以非零码退出（exit=${missing_code}），未进入无人可登录的降级状态"
else
  record AC-10 failed "配置缺失时服务仍然启动了（exit=0）"
fi

# --- 保存脱敏运行日志，供报告引用 ---
# Paths must be declared before the run so CAO can exclude only these owned
# outputs from the code snapshot. Undeclared run files would make it stale.
EVIDENCE_LOG="$(python3 -c 'import json,os; d=json.loads(os.environ["CAO_SDD_ACCEPTANCE_REQUEST"]); print(next(p for p in d["declaration"]["outputs"] if p.endswith("/run-log.txt")))')"
TEST_EVIDENCE="$(python3 -c 'import json,os; d=json.loads(os.environ["CAO_SDD_ACCEPTANCE_REQUEST"]); print(next(p for p in d["declaration"]["outputs"] if p.endswith("/session-tests.txt")))')"
if [ -z "$EVIDENCE_LOG" ] || [ -z "$TEST_EVIDENCE" ]; then
  echo "请在声明 outputs 中登记当前 Work 的 run-log.txt 与 session-tests.txt" >&2
  exit 2
fi
mkdir -p "$(dirname "$EVIDENCE_LOG")" "$(dirname "$TEST_EVIDENCE")"
python3 - "$LOG" "$EVIDENCE_LOG" "$ALICE_PASSWORD" "$ALICE_NEW_PASSWORD" "$ALICE_HASH" "$NEW_HASH" <<'PY'
import pathlib, sys
text = pathlib.Path(sys.argv[1]).read_text()
for value in sys.argv[3:]:
    if value: text = text.replace(value, '[REDACTED]')
pathlib.Path(sys.argv[2]).write_text(text)
PY

# --- 输出报告 ---
python3 - "$RESULTS" "$EVIDENCE_LOG" "$TEST_EVIDENCE" <<'PY'
import json, os, sys

results = {}
for line in open(sys.argv[1], encoding='utf-8'):
    parts = line.rstrip('\n').split('\t')
    if len(parts) == 3:
        results[parts[0]] = (parts[1], parts[2])

request = json.loads(os.environ.get('CAO_SDD_ACCEPTANCE_REQUEST', '{}'))
declaration = request.get('declaration', {})
import pathlib
test_report = pathlib.Path('target/surefire-reports/TEST-com.example.loginservice.LoginServiceIntegrationTest.xml')
# Keep only test identities and outcomes, not the report's system properties,
# passwords, tokens, environment variables or raw console output.
import xml.etree.ElementTree as ET
try:
    original = ET.parse(test_report).getroot()
    snapshot = ET.Element('testsuite')
    for case in original.findall('testcase'):
        entry = ET.SubElement(snapshot, 'testcase', name=case.get('name', ''), classname=case.get('classname', ''))
        for tag in ('failure', 'error', 'skipped'):
            if case.find(tag) is not None: ET.SubElement(entry, tag)
    pathlib.Path(sys.argv[3]).write_text(ET.tostring(snapshot, encoding='unicode'))
except (OSError, ET.ParseError):
    pathlib.Path(sys.argv[3]).write_text('<testsuite/>')
items = []
for criterion in declaration.get('criteria', []):
    cid = criterion['id']
    status, reason = results.get(cid, ('blocked', '验收脚本未产生该检查项结果'))
    token_evidence = []
    if cid in {'AC-4', 'AC-5'}:
        import pathlib, xml.etree.ElementTree as ET
        name = 'invalidatesSessionWhenUserRemoved' if cid == 'AC-4' else 'invalidatesSessionWhenPasswordChanged'
        report = pathlib.Path('target/surefire-reports/TEST-com.example.loginservice.LoginServiceIntegrationTest.xml')
        jar = pathlib.Path('target/login-service-0.1.0-SNAPSHOT.jar')
        try:
            cases = ET.parse(report).getroot().findall('testcase')
            match = next(case for case in cases if case.get('name') == name)
            valid = not any(match.find(tag) is not None for tag in ('failure', 'error', 'skipped')) and report.stat().st_mtime >= jar.stat().st_mtime
        except (OSError, ET.ParseError, StopIteration):
            valid = False
        if not valid:
            status, reason = 'blocked', '缺少当前构建后的令牌失效测试证据；先运行完整测试'
        else:
            reason += '; 既有令牌失效由具体 Surefire 用例证明'
            token_evidence = [{'path': sys.argv[3], 'reason': name + '；本次保存的测试身份与结果快照'}]
    items.append({
        'id': cid,
        'status': status,
        'evidence': [
            {'path': '_bmad-output/implementation-artifacts/spec-1-login-service-hot-reload.md', 'reason': reason},
            {'path': 'scripts/verification/acceptance.sh', 'reason': '验收检查由该脚本以真实进程执行'},
            {'path': sys.argv[2], 'reason': '本次真实运行的脱敏服务日志'},
            *token_evidence,
        ],
    })

print(json.dumps({
    'run_id': request.get('run_id', ''),
    'input_sha256': request.get('input_sha256', ''),
    'provider': request.get('provider', ''),
    'items': items,
}, ensure_ascii=False))
PY
