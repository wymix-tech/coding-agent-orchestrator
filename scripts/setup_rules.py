"""Small composable identity/layout rules; explicit plans cover other toolchains.

These rules are conveniences, not a language admission list. No core gate
branches on language or assumes all products have a package name or compiler.
"""
import json
import keyword
import re

JAVA_KEYWORDS = set('abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally float for goto if implements import instanceof int interface long native new package private protected public return short static strictfp super switch synchronized this throw throws transient try void volatile while true false null _'.split())
RUST_KEYWORDS = set('as break const continue crate else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while async await dyn abstract become box do final macro override priv typeof unsized virtual yield try'.split())
CSHARP_KEYWORDS = set('abstract as base bool break byte case catch char checked class const continue decimal default delegate do double else enum event explicit extern false finally fixed float for foreach goto if implicit in int interface internal is lock long namespace new null object operator out override params private protected public readonly ref return sbyte sealed short sizeof stackalloc static string struct switch this throw true try typeof uint ulong unchecked unsafe ushort using virtual void volatile while'.split())

RULES = {
    'python-unittest': {'language': 'python', 'fields': ['name', 'import_name'], 'layout': 'flat', 'capabilities': ['load', 'test']},
    'node-test': {'language': 'javascript', 'fields': ['name'], 'layout': 'src', 'capabilities': ['load', 'test']},
    'typescript-node': {'language': 'typescript', 'fields': ['name', 'typescript_version'], 'layout': 'src', 'capabilities': ['build', 'test']},
    'go-module': {'language': 'go', 'fields': ['name', 'module_path', 'go_version'], 'layout': 'library', 'capabilities': ['build', 'test']},
    'java-maven': {'language': 'java', 'fields': ['name', 'group_id', 'artifact_id', 'namespace', 'java_version', 'junit_version'], 'layout': 'jvm', 'capabilities': ['build', 'test']},
    'java-gradle': {'language': 'java', 'fields': ['name', 'group_id', 'namespace', 'java_version', 'junit_version'], 'layout': 'jvm', 'capabilities': ['build', 'test']},
    'rust-cargo': {'language': 'rust', 'fields': ['name', 'crate_name', 'edition'], 'layout': 'library', 'capabilities': ['build', 'test']},
    'dotnet-library': {'language': 'c#', 'fields': ['name', 'namespace', 'target_framework'], 'layout': 'library', 'capabilities': ['build', 'test']},
    'cpp-cmake': {'language': 'c++', 'fields': ['name', 'target_name', 'cpp_standard'], 'layout': 'library', 'capabilities': ['build', 'test']},
}

for _rule, _constraints in {
    'python-unittest': {'test_framework': ['unittest', 'python unittest']},
    'node-test': {'build_tool': ['npm'], 'test_framework': ['node:test', 'node test'], 'package_manager': ['npm']},
    'typescript-node': {'build_tool': ['npm', 'tsc', 'typescript'], 'test_framework': ['node:test', 'node test'], 'package_manager': ['npm']},
    'go-module': {'build_tool': ['go', 'go modules'], 'test_framework': ['testing', 'go test']},
    'java-maven': {'build_tool': ['maven', 'mvn'], 'test_framework': ['junit', 'junit5', 'junit jupiter']},
    'java-gradle': {'build_tool': ['gradle'], 'test_framework': ['junit', 'junit5', 'junit jupiter']},
    'rust-cargo': {'build_tool': ['cargo'], 'test_framework': ['cargo test', 'rust test']},
    'dotnet-library': {'build_tool': ['dotnet', 'msbuild'], 'test_framework': ['console assertions']},
    'cpp-cmake': {'build_tool': ['cmake'], 'test_framework': ['ctest']},
}.items():
    RULES[_rule]['technology'] = _constraints


def describe(rule=None):
    if rule is None:
        return {key: {'id': key, 'version': 1, **value} for key, value in RULES.items()}
    if rule not in RULES:
        raise ValueError('PROJECT_SETUP_EXPLICIT_PLAN_REQUIRED: use a plan with exact files and checks; no built-in rule is required')
    return {'id': rule, 'version': 1, **RULES[rule]}


def validate_identity(rule, identity, *, partial=False):
    spec = describe(rule)
    if not isinstance(identity, dict):
        raise ValueError('PROJECT_IDENTITY_INVALID')
    if partial and any(field not in spec['fields'] for field in identity):
        raise ValueError('PROJECT_IDENTITY_FIELD_INVALID')
    for field in identity if partial else spec['fields']:
        value = identity.get(field)
        if not isinstance(value, str) or not value or any(c in value for c in '\x00\n\r\\'):
            raise ValueError('PROJECT_IDENTITY_REQUIRED: ' + field)
        if field in {'namespace', 'import_name', 'crate_name', 'target_name'}:
            if not all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', p) for p in value.split('.')):
                raise ValueError('PROJECT_IDENTITY_INVALID: ' + field)
            parts = value.split('.')
            reserved = (JAVA_KEYWORDS if rule.startswith('java-') else CSHARP_KEYWORDS if rule == 'dotnet-library'
                        else RUST_KEYWORDS if rule == 'rust-cargo' else set(keyword.kwlist) if rule == 'python-unittest' else set())
            if field == 'crate_name' and len(parts) != 1:
                raise ValueError('PROJECT_IDENTITY_INVALID: crate_name')
            if set(parts) & reserved:
                raise ValueError('PROJECT_IDENTITY_RESERVED: ' + field)
        elif field == 'module_path':
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9./_~-]*', value) or any(p in {'', '.', '..'} for p in value.split('/')):
                raise ValueError('PROJECT_IDENTITY_INVALID: module_path')
        elif field == 'name' and rule in {'node-test', 'typescript-node'}:
            if len(value) > 214 or not re.fullmatch(r'(?:@[a-z0-9_-]+/)?[a-z0-9][a-z0-9._-]*', value):
                raise ValueError('PROJECT_IDENTITY_INVALID: npm name')
        elif field == 'name' and rule == 'python-unittest':
            if not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?', value):
                raise ValueError('PROJECT_IDENTITY_INVALID: distribution name')
        elif field == 'name' and rule == 'rust-cargo':
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]*', value):
                raise ValueError('PROJECT_IDENTITY_INVALID: Cargo package name')
        elif not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', value):
            raise ValueError('PROJECT_IDENTITY_INVALID: ' + field)
    return spec


def scaffold(rule, identity):
    spec = validate_identity(rule, identity)
    i = identity
    files, checks, outputs = [], [], []
    def put(path, content, purpose):
        files.append({'path': path, 'content': content, 'purpose': purpose})
    def check(name, command, capability):
        checks.append({'name': name, 'command': command, 'capability': capability})
    if rule == 'python-unittest':
        module = i['import_name']
        put('pyproject.toml', '[project]\nname = ' + json.dumps(i['name']) + '\nversion = "0.1.0"\n', 'manifest')
        put(module.replace('.', '/') + '/__init__.py', 'def ready():\n    return True\n', 'entry')
        put('tests/test_baseline.py', 'import unittest\nfrom ' + module + ' import ready\n\nclass Baseline(unittest.TestCase):\n    def test_load(self):\n        self.assertTrue(ready())\n', 'test')
        check('load', 'python3 -c "from ' + module + ' import ready; assert ready()"', 'load')
        check('test', 'python3 -m unittest discover -s tests -v', 'test')
        outputs = []  # Existing recursive cache policy already owns __pycache__.
    elif rule in {'node-test', 'typescript-node'}:
        ts = rule == 'typescript-node'
        package = {'name': i['name'], 'version': '0.1.0', 'private': True, 'type': 'module',
                   'scripts': {'test': 'node --test --test-reporter=tap tests/baseline.test.mjs'}}
        if ts:
            package.update(devDependencies={'typescript': i['typescript_version']})
            package['scripts']['build'] = 'tsc'
            put('tsconfig.json', json.dumps({'compilerOptions': {'target': 'ES2022', 'module': 'NodeNext', 'outDir': 'dist', 'strict': True}, 'include': ['src/*.ts']}, indent=2) + '\n', 'manifest')
        put('package.json', json.dumps(package, indent=2) + '\n', 'manifest')
        put('src/index.' + ('ts' if ts else 'mjs'), 'export function ready() { return true; }\n', 'entry')
        put('tests/baseline.test.mjs', "import test from 'node:test';\nimport assert from 'node:assert/strict';\nimport { ready } from '../" + ('dist/index.js' if ts else 'src/index.mjs') + "';\ntest('module loads', () => { assert.equal(ready(), true); });\n", 'test')
        if ts:
            check('build', 'npm run build', 'build')
        else:
            check('load', "node -e \"import('./src/index.mjs').then(m => { if (!m.ready()) process.exit(1); })\"", 'load')
        check('test', 'npm test', 'test')
        outputs = ['dist/'] if ts else []
    elif rule == 'go-module':
        put('go.mod', 'module ' + i['module_path'] + '\n\ngo ' + i['go_version'] + '\n', 'manifest')
        put('baseline.go', 'package baseline\n\nfunc Ready() bool { return true }\n', 'entry')
        put('baseline_test.go', 'package baseline\nimport "testing"\nfunc TestReady(t *testing.T) {\n if !Ready() { t.Fatal("module not ready") }\n}\n', 'test')
        check('build', 'go build ./...', 'build')
        check('test', 'go test -v -count=1 ./...', 'test')
    elif rule in {'java-maven', 'java-gradle'}:
        ns, java, junit = i['namespace'], i['java_version'], i['junit_version']
        path = ns.replace('.', '/')
        put('src/main/java/' + path + '/Baseline.java', 'package ' + ns + ';\npublic final class Baseline {\n public static boolean ready() { return true; }\n}\n', 'entry')
        put('src/test/java/' + path + '/BaselineTest.java', 'package ' + ns + ';\nimport org.junit.jupiter.api.Test;\nimport static org.junit.jupiter.api.Assertions.assertTrue;\nclass BaselineTest {\n @Test void loads() { assertTrue(Baseline.ready()); }\n}\n', 'test')
        if rule == 'java-maven':
            put('pom.xml', '<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>\n<groupId>' + i['group_id'] + '</groupId><artifactId>' + i['artifact_id'] + '</artifactId><version>0.1.0</version>\n<properties><maven.compiler.release>' + java + '</maven.compiler.release></properties>\n<dependencies><dependency><groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId><version>' + junit + '</version><scope>test</scope></dependency></dependencies>\n<build><plugins><plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-compiler-plugin</artifactId><version>3.13.0</version></plugin><plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-surefire-plugin</artifactId><version>3.5.2</version><configuration><failIfNoTests>true</failIfNoTests></configuration></plugin></plugins></build></project>\n', 'manifest')
            check('build', 'mvn -B compile', 'build')
            check('test', 'mvn -B test', 'test')
            outputs = ['target/']
        else:
            put('settings.gradle', "rootProject.name = '" + i['name'] + "'\n", 'manifest')
            put('build.gradle', "plugins { id 'java-library' }\ngroup = '" + i['group_id'] + "'\nversion = '0.1.0'\nrepositories { mavenCentral() }\njava { toolchain { languageVersion = JavaLanguageVersion.of(" + java + ") } }\ndependencies { testImplementation 'org.junit.jupiter:junit-jupiter:" + junit + "'; testRuntimeOnly 'org.junit.platform:junit-platform-launcher' }\ntest { useJUnitPlatform(); testLogging { events 'passed', 'failed', 'skipped' }; afterSuite { desc, result -> if (!desc.parent) println(\"Tests run: ${result.testCount}, Failures: ${result.failedTestCount}, Skipped: ${result.skippedTestCount}\") } }\n", 'manifest')
            check('build', 'gradle --no-daemon classes --console=plain', 'build')
            check('test', 'gradle --no-daemon test --console=plain --rerun-tasks', 'test')
            outputs = ['build/', '.gradle/']
    elif rule == 'rust-cargo':
        put('Cargo.toml', '[package]\nname = ' + json.dumps(i['name']) + '\nversion = "0.1.0"\nedition = ' + json.dumps(i['edition']) + '\n[lib]\nname = ' + json.dumps(i['crate_name']) + '\n', 'manifest')
        put('src/lib.rs', 'pub fn ready() -> bool { true }\n#[cfg(test)] mod tests {\n #[test] fn loads() { assert!(super::ready()); }\n}\n', 'entry')
        check('build', 'cargo build', 'build')
        check('test', 'cargo test', 'test')
        outputs = ['target/']
    elif rule == 'dotnet-library':
        put(i['name'] + '.csproj', '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>' + i['target_framework'] + '</TargetFramework><RootNamespace>' + i['namespace'] + '</RootNamespace></PropertyGroup><ItemGroup><Compile Remove="tests/**/*.cs" /></ItemGroup></Project>\n', 'manifest')
        put('Baseline.cs', 'namespace ' + i['namespace'] + ' { public class Baseline { public bool Ready() { return true; } } }\n', 'entry')
        check('build', 'dotnet build', 'build')
        outputs = ['bin/', 'obj/']
        put('tests/BaselineTests.csproj', '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>' + i['target_framework'] + '</TargetFramework></PropertyGroup><ItemGroup><ProjectReference Include="../' + i['name'] + '.csproj" /></ItemGroup></Project>\n', 'manifest')
        put('tests/Program.cs', 'if (!new ' + i['namespace'] + '.Baseline().Ready()) { throw new System.Exception("Baseline failed"); }\nSystem.Console.WriteLine("Tests run: 1, Failures: 0, Errors: 0");\n', 'test')
        check('test', 'dotnet run --project tests/BaselineTests.csproj', 'test')
    elif rule == 'cpp-cmake':
        target = i['target_name']
        put('CMakeLists.txt', 'cmake_minimum_required(VERSION 3.16)\nproject(' + target + ' LANGUAGES CXX)\nset(CMAKE_CXX_STANDARD ' + i['cpp_standard'] + ')\nenable_testing()\nadd_executable(' + target + ' tests/baseline.cpp)\nadd_test(NAME baseline COMMAND ' + target + ')\n', 'manifest')
        put('tests/baseline.cpp', '#include <cassert>\nint main() { assert(2 + 2 == 4); return 0; }\n', 'test')
        check('build', 'cmake -S . -B build && cmake --build build', 'build')
        check('test', 'ctest --test-dir build --output-on-failure', 'test')
        outputs = ['build/']
    return {'schema_version': 1, 'rule': {'id': rule, 'version': spec['version']}, 'identity': identity,
            'files': files, 'checks': checks, 'outputs': outputs,
            **({'generated_output_suffixes': {'dist/': ['.js']}} if rule == 'typescript-node' else {}),
            **({'generated_output_suffixes': {'build/': ['.js']}} if rule == 'java-gradle' else {}),
            'required_capabilities': spec['capabilities']}
