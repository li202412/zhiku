import { FileText } from 'lucide-react'

interface EmptyStateProps {
  icon?: React.ReactNode
  message?: string
  action?: React.ReactNode
}

export default function EmptyState({ icon, message, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-16 px-4">
      <div className="text-[var(--color-text-tertiary)] mb-4">
        {icon || <FileText size={48} />}
      </div>
      <p className="text-sm text-[var(--color-text-secondary)] mb-4">
        {message}
      </p>
      {action}
    </div>
  )
}
