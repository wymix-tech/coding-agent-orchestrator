# Spring Boot architecture adoption and first Work

This walkthrough uses accepted Java/Spring Boot/Maven/JUnit project context,
namespace `com.example.shop`, and a user-selected `spring-boot-modular-ddd` style.
Obtain actual user evidence using project inspect; do not fabricate the ID.

```bash
cao project architecture list
cao project architecture draft --template spring-boot-modular-ddd \
  --variable source_root=src/main/java/com/example/shop \
  --variable test_root=src/test/java/com/example/shop
cao project architecture validate
cao project architecture diff
cao project architecture apply --evidence <observed-choice-id> --expected-sha256 <diff-current-hash>
cao project architecture show
```

Complete the normal identity interview and exact setup plan/check. Adapt the
prepared setup input using the accepted namespace; Application.java belongs in
the base package for scanning. Run project verification preparation before Work.
The architecture CLI does not generate business classes or claim Maven passed.

For a requirement-owned create-order Work, plan these files:

| Role | Relative to src/main/java/com/example/shop/ |
| --- | --- |
| HTTP adapter | order/web/controller/OrderController.java |
| HTTP data | order/web/request/CreateOrderRequest.java; order/web/response/CreateOrderResponse.java |
| Application input/output | order/application/command/CreateOrderCommand.java; order/application/result/CreateOrderResult.java |
| Transaction orchestration | order/application/service/CreateOrderService.java |
| Aggregate | order/domain/model/Order.java |
| Storage port | order/domain/repository/OrderRepository.java |
| JPA data | order/infrastructure/persistence/entity/OrderEntity.java |
| Mapping | order/infrastructure/persistence/mapper/OrderPersistenceMapper.java |
| Spring Data adapter | order/infrastructure/persistence/repository/SpringDataOrderRepository.java |
| Port implementation | order/infrastructure/persistence/repository/JpaOrderRepository.java |

Web depends on application. Application depends on domain. Infrastructure depends
on domain and implements its repository port. Spring injects the adapter into the
application service through the port; domain imports no framework/persistence types.
Map request to command, domain to application result, result to HTTP response, and
domain to/from persistence entity in the adapter. Keep the transaction in the
application service. Mirror these boundaries in src/test/java/com/example/shop/.

Run the native workflow's actual tests and review, then `cao project architecture
check` and `cao verify`. A flat OrderController.java in the base package fails
ARCH-STRUCT-001. A web controller importing infrastructure persistence fails
ARCH-DEP-001. A clean arrangement passes supported structural checks; provider
review still assesses business responsibilities and semantic dependencies.

After changing a rule, inspect/accept its diff and refresh the affected Work with
`cao project bind --refresh --work <id>`. Re-evaluate native plans, reviews and
technical checks. Resume retains the same Work and progress, while old evidence
cannot be reused across an architecture change.
