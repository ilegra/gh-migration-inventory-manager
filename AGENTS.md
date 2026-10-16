# Agent Development & Architecture Guidelines

This document outlines key architectural principles and testing conventions to adhere to across the codebase. These guidelines apply universally to any module and file.

---

## Coding Guidelines
### 1. Data Classes & Separation of Concerns

- **Pure Data Containers**: Data classes, models, and data transfer objects (DTOs) should represent state and hold data only. They must not contain business rules, validation workflows, or external serialization logic.
- **Service/Client Encapsulation**: Any formatting, parsing, padding, or protocol-specific serialization rules for external systems belong inside the responsible client/service (as private helper methods) or within dedicated converter components, never inside the data models.

### 2. Code Comments & Documentation

- **Minimal & Purposeful Comments**: Code comments should only be added in public methods or complex logic and is not reasonable only looking at the code.
- **Self-Documenting Code**: Avoid redundant comments that merely restate what readable code is doing.
- **Public API Documentation**: Use clear, concise docstrings on public classes, methods, and functions to describe parameters, return types, and exceptions.

### 3. Testing Principles

- **Unit tests Scope**: Unit tests should not access external dependencies like Google API either databases. These external dependencies should be mocked  and their behavior similated during test execution to allow code verification.
- **Integration test scope**: Integration tests must access external dependencies and validate the integration between external dependencies and current implemnation. Avoid paralel execution since it because the shared state.
- **Test Exclusively via Public Methods**: Tess must only exercise behaviors through public methods and public APIs. Never test, invoke, or inspect private methods (`_method`) in unit tests.


---
