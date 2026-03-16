import { Modal } from '../../components/ui'

export default function RunCancelModal({
  isOpen,
  canceling,
  onClose,
  onConfirm,
}) {
  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Cancel Run"
      size="sm"
    >
      <div className="space-y-4">
        <p className="text-terminal-200">
          Are you sure you want to cancel this run? This action cannot be undone.
        </p>
        <div className="flex justify-end gap-3">
          <button onClick={onClose} className="btn btn-secondary">
            Keep Running
          </button>
          <button onClick={onConfirm} disabled={canceling} className="btn btn-danger">
            {canceling ? 'Canceling...' : 'Cancel Run'}
          </button>
        </div>
      </div>
    </Modal>
  )
}
