// Two-step buttons for destructive actions: the first click shows the question from
// data-confirm on the button, a second click within a few seconds runs the action.
const CONFIRM_TIMEOUT_MS = 4000;

function confirmClick(button, action) {
    if (button.dataset.confirming === 'true') {
        resetConfirm(button);
        return action();
    }
    button.dataset.confirming = 'true';
    button.dataset.originalHtml = button.innerHTML;
    button.classList.add('confirming');
    const question = document.createElement('span');
    question.className = 'confirm-text';
    question.textContent = button.dataset.confirm;
    button.replaceChildren(question);
    button._confirmTimer = setTimeout(() => resetConfirm(button), CONFIRM_TIMEOUT_MS);
}

function resetConfirm(button) {
    clearTimeout(button._confirmTimer);
    if (button.dataset.confirming !== 'true') return;
    button.dataset.confirming = 'false';
    button.classList.remove('confirming');
    button.innerHTML = button.dataset.originalHtml;
}
