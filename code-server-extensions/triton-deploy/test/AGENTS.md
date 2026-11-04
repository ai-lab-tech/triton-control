# Test conventions

- Structure every new or modified test using Arrange–Act–Assert (AAA).
- Mark the three phases with `# Arrange`, `# Act`, and `# Assert` in Python,
  or `// Arrange`, `// Act`, and `// Assert` in JavaScript.
- Put fixtures, stubs, inputs and cleanup registration in Arrange; invoke the
  behavior in Act; check outcomes in Assert. Keep assertions out of setup callbacks.
- Split independent scenarios into separate tests. Parameterized cases should
  each follow AAA. Exception assertions may wrap the action when required by the
  test framework; check relevant error details in the Assert section.
