; TypeScript surface area. Same three capture families as python.scm.
; This file is the design document's first cut line: deleting it leaves a
; complete Python-only system and the abstraction stays visible.

; --- definitions -------------------------------------------------------------
(function_declaration) @def.function
(generator_function_declaration) @def.function
(class_declaration) @def.class
(method_definition) @def.method
(interface_declaration) @def.type
(type_alias_declaration) @def.type
(enum_declaration) @def.type

; const foo = () => {} and const BAR = 1 both land here; the kind is refined by
; whether the initialiser is a function.
(lexical_declaration (variable_declarator) @def.const)

; --- imports -----------------------------------------------------------------
(import_statement) @import

; --- calls -------------------------------------------------------------------
(call_expression) @call

; --- dynamic dispatch --------------------------------------------------------
(subscript_expression) @dynamic.subscript
