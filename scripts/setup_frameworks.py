"""Framework-aware scaffold adapters; framework releases are never invented."""
import re
from xml.sax.saxutils import escape


def spring_boot(framework):
    from scripts.project_sources import compact
    return compact(framework) == 'springboot'


def release(value):
    return bool(re.fullmatch(r'\d+\.\d+\.\d+(?:[-.][A-Za-z0-9]+)*', value))


def manifest_release(files):
    """Inspect the actual literal parent/plugin version, not a matching comment."""
    import xml.etree.ElementTree as ET
    for entry in files:
        if entry.get('purpose') != 'manifest':
            continue
        name = entry.get('path', '').rsplit('/', 1)[-1]
        text = entry.get('content', '')
        if name == 'pom.xml':
            try:
                root = ET.fromstring(text)
                parent = next((node for node in root if node.tag.rsplit('}', 1)[-1] == 'parent'), None)
                fields = {node.tag.rsplit('}', 1)[-1]: node.text for node in parent} if parent is not None else {}
            except ET.ParseError:
                return None
            if fields.get('groupId') == 'org.springframework.boot' and fields.get('artifactId') == 'spring-boot-starter-parent':
                return fields.get('version')
        elif name in {'build.gradle', 'build.gradle.kts'}:
            match = re.search(r"id\s*(?:\(\s*)?['\"]org\.springframework\.boot['\"]\s*\)?\s+version\s+['\"]([^'\"]+)['\"]", text)
            if match:
                return match[1]
    return None


def scaffold(rule, identity):
    """The caller has validated exact release/identity provenance first."""
    ns, java, junit, boot = (identity[k] for k in ('namespace', 'java_version', 'junit_version', 'framework_version'))
    if not release(boot):
        raise ValueError('PROJECT_FRAMEWORK_VERSION_REQUIRED: framework_version; ask for a concrete Spring Boot release')
    if boot.split('.', 1)[0] != '3':
        raise ValueError('PROJECT_SETUP_EXPLICIT_PLAN_REQUIRED: this adapter supports Spring Boot 3; preserve the selected release and use an explicit framework plan for other major versions')
    if junit != '5' and not release(junit):
        raise ValueError('PROJECT_DEPENDENCY_VERSION_REQUIRED: junit_version; choose BOM-managed 5 or an exact release')
    if junit.split('.', 1)[0] != '5':
        raise ValueError('PROJECT_SETUP_RULE_CONTEXT_MISMATCH: this Spring Boot adapter supports JUnit 5; use an explicit plan for another generation')
    files = [
        {'path': 'src/main/java/' + ns.replace('.', '/') + '/Application.java', 'purpose': 'entry',
         'content': 'package ' + ns + ';\n\nimport org.springframework.boot.SpringApplication;\nimport org.springframework.boot.autoconfigure.SpringBootApplication;\n\n@SpringBootApplication\npublic class Application {\n    public static void main(String[] args) {\n        SpringApplication.run(Application.class, args);\n    }\n}\n'},
        {'path': 'src/test/java/' + ns.replace('.', '/') + '/ApplicationTests.java', 'purpose': 'test',
         'content': 'package ' + ns + ';\n\nimport org.junit.jupiter.api.Test;\nimport org.springframework.boot.test.context.SpringBootTest;\n\n@SpringBootTest\nclass ApplicationTests {\n    @Test\n    void contextLoads() {\n    }\n}\n'}]
    if rule == 'java-maven':
        properties = '<java.version>' + java + '</java.version>'
        if junit != '5':
            properties += '<junit-jupiter.version>' + junit + '</junit-jupiter.version>'
        pom = ('<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion>\n'
               '<parent><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-parent</artifactId><version>' + boot + '</version><relativePath/></parent>\n'
               '<groupId>' + identity['group_id'] + '</groupId><artifactId>' + identity['artifact_id'] + '</artifactId><version>0.1.0-SNAPSHOT</version>\n'
               '<name>' + escape(identity['name']) + '</name>\n<properties>' + properties + '</properties>\n'
               '<dependencies><dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter</artifactId></dependency>'
               '<dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-test</artifactId><scope>test</scope></dependency></dependencies>\n'
               '<build><plugins><plugin><groupId>org.springframework.boot</groupId><artifactId>spring-boot-maven-plugin</artifactId></plugin>'
               '<plugin><groupId>org.apache.maven.plugins</groupId><artifactId>maven-surefire-plugin</artifactId><configuration><failIfNoTests>true</failIfNoTests></configuration></plugin>'
               '</plugins></build></project>\n')
        files.append({'path': 'pom.xml', 'purpose': 'manifest', 'content': pom})
        commands = [('build', 'mvn -B compile', 'build'), ('framework-load', 'mvn -B -Dtest=ApplicationTests test', 'load'),
                    ('test', 'mvn -B test', 'test')]
        outputs = ['target/']
    else:
        gradle = ("plugins { id 'java'; id 'org.springframework.boot' version '" + boot + "' }\n"
                  "group = '" + identity['group_id'] + "'\nversion = '0.1.0-SNAPSHOT'\nrepositories { mavenCentral() }\n"
                  "java { toolchain { languageVersion = JavaLanguageVersion.of(" + java + ") } }\ndependencies {\n"
                  "    implementation platform('org.springframework.boot:spring-boot-dependencies:" + boot + "')\n"
                  "    implementation 'org.springframework.boot:spring-boot-starter'\n"
                  "    testImplementation 'org.springframework.boot:spring-boot-starter-test'\n"
                  + ("    testImplementation enforcedPlatform('org.junit:junit-bom:" + junit + "')\n" if junit != '5' else '')
                  + "    testRuntimeOnly 'org.junit.platform:junit-platform-launcher'\n}\n"
                  "test { useJUnitPlatform(); testLogging { events 'passed', 'failed', 'skipped' }; afterSuite { desc, result -> if (!desc.parent) println(\"Tests run: ${result.testCount}, Failures: ${result.failedTestCount}, Skipped: ${result.skippedTestCount}\") } }\n")
        files += [{'path': 'build.gradle', 'purpose': 'manifest', 'content': gradle},
                  {'path': 'settings.gradle', 'purpose': 'manifest', 'content': "rootProject.name = '" + identity['name'] + "'\n"}]
        commands = [('build', 'gradle --no-daemon classes --console=plain', 'build'),
                    ('framework-load', 'gradle --no-daemon test --tests ApplicationTests --console=plain --rerun-tasks', 'load'),
                    ('test', 'gradle --no-daemon test --console=plain --rerun-tasks', 'test')]
        outputs = ['build/', '.gradle/']
    return {'files': files, 'checks': [{'name': n, 'command': c, 'capability': cap} for n, c, cap in commands],
            'outputs': outputs, 'required_capabilities': ['build', 'load', 'test'], 'framework_support': 'Spring Boot'}
