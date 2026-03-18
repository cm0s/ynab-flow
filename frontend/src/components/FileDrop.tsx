import { useState, useRef, type DragEvent, type ChangeEvent } from 'react';
import { Upload, FileText } from 'lucide-react';

interface FileDropProps {
  onFile: (file: File) => void;
  disabled?: boolean;
}

export default function FileDrop({ onFile, disabled }: FileDropProps) {
  const [dragover, setDragover] = useState(false);
  const [fileName, setFileName] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = (file: File) => {
    setFileName(file.name);
    onFile(file);
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragover(false);
    if (disabled) return;
    const file = e.dataTransfer.files?.[0];
    if (file) handleFile(file);
  };

  const onChange = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
  };

  return (
    <div
      className={`file-drop ${dragover ? 'dragover' : ''}`}
      onDragOver={(e) => { e.preventDefault(); setDragover(true); }}
      onDragLeave={() => setDragover(false)}
      onDrop={onDrop}
      onClick={() => !disabled && inputRef.current?.click()}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".csv"
        style={{ display: 'none' }}
        onChange={onChange}
        disabled={disabled}
      />
      <div className="file-drop-icon">
        {fileName ? <FileText size={48} /> : <Upload size={48} />}
      </div>
      {fileName ? (
        <>
          <h3 style={{ marginBottom: 4 }}>{fileName}</h3>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
            Click or drop to replace
          </p>
        </>
      ) : (
        <>
          <h3 style={{ marginBottom: 4 }}>Drop your bank CSV here</h3>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>
            or click to browse — supports PostFinance / BCGE format
          </p>
        </>
      )}
    </div>
  );
}
