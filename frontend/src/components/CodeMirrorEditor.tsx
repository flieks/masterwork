import CodeMirror from '@uiw/react-codemirror';
import { markdown, markdownLanguage } from '@codemirror/lang-markdown';
import { EditorView } from '@codemirror/view';
import { githubLight, githubDark } from '@uiw/codemirror-theme-github';
import { usePrefersDark } from '~/lib/hooks';
import { cn } from '~/lib/utils';
import type { CodeEditorProps } from './CodeEditor';

/** Heavy CodeMirror implementation — lazily loaded via `CodeEditor`. */
export default function CodeMirrorEditor({
  value,
  onChange,
  readOnly = false,
  minHeight = '24rem',
  maxHeight,
  className,
  ariaLabel,
}: CodeEditorProps) {
  const dark = usePrefersDark();

  return (
    <div
      className={cn('overflow-hidden rounded-md border text-sm', className)}
      aria-label={ariaLabel}
    >
      <CodeMirror
        value={value}
        onChange={onChange}
        readOnly={readOnly}
        editable={!readOnly}
        theme={dark ? githubDark : githubLight}
        minHeight={minHeight}
        maxHeight={maxHeight}
        extensions={[
          markdown({ base: markdownLanguage, codeLanguages: [] }),
          EditorView.lineWrapping,
        ]}
        basicSetup={{
          lineNumbers: true,
          foldGutter: false,
          highlightActiveLine: !readOnly,
          highlightActiveLineGutter: !readOnly,
        }}
      />
    </div>
  );
}
