# Required Formatting Shapes

These formatting shapes must be produced by the code. They are
recorded design decisions. We can never change the logic or unit
tests in such a way as these shapes are broken.

* `module` statement : If the module statement is rendered multi-line,
the `exposing` keyword stays on the same line as `module` and is the last
word on the line. The list of exposed symbols starts on the next line.

* `import` statement : If an import statement is rendered multi-line,
the `exposing` keyword stays on the same line as `import` and is the last
word on the line. The list of exposed symbols starts on the next line.

