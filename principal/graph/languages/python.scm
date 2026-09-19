; Python surface area for the code graph. Three capture families, and nothing
; else in the system knows this language exists. Adding a grammar means writing
; one of these files, which is what makes the TypeScript cut line a clean cut
; rather than a refactor.

; --- definitions -------------------------------------------------------------
(function_definition) @def.function
(class_definition) @def.class

; Module-level constants only. An assignment inside a function body is a local,
; not part of the interface, and including them buries the real symbols.
(module (expression_statement (assignment) @def.const))

; --- imports -----------------------------------------------------------------
(import_statement) @import
(import_from_statement) @import

; --- calls -------------------------------------------------------------------
(call) @call

; Dynamic dispatch, recorded so the blast radius can admit what it cannot prove.
; getattr(obj, "name")() and registry["name"]() reach a symbol with no static
; edge to show for it, and the PR risk list says so out loud.
(call function: (identifier) @dynamic.builtin
  (#match? @dynamic.builtin "^(getattr|globals|locals|eval|exec|__import__)$")) @dynamic
(subscript) @dynamic.subscript
