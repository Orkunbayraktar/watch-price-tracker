document.addEventListener("DOMContentLoaded", () => {
	const shell = document.querySelector("[data-shell]");
	const sidebarToggle = document.querySelector("[data-sidebar-toggle]");
	if (shell && sidebarToggle) {
		sidebarToggle.addEventListener("click", () => {
			const isOpen = shell.classList.toggle("is-sidebar-open");
			sidebarToggle.setAttribute("aria-expanded", String(isOpen));
		});
	}

	document.querySelectorAll("form[data-submit-once]").forEach((form) => {
		form.addEventListener("submit", (event) => {
			if (form.dataset.submitting === "true") {
				event.preventDefault();
				return;
			}
			form.dataset.submitting = "true";
			form.setAttribute("aria-busy", "true");
			const submitter = event.submitter || form.querySelector("button[type='submit']");
			if (submitter) {
				submitter.disabled = true;
				submitter.textContent = submitter.dataset.submittingLabel || "Working...";
			}
		});
	});

	document.querySelectorAll("form[data-confirm-message]").forEach((form) => {
		form.addEventListener("submit", (event) => {
			if (!window.confirm(form.dataset.confirmMessage)) {
				event.preventDefault();
			}
		});
	});

	const selectAll = document.querySelector("[data-select-all]");
	const itemCheckboxes = Array.from(document.querySelectorAll("[data-item-select]:not(:disabled)"));
	const selectionCount = document.querySelector("[data-selection-count]");
	const updateSelectionState = () => {
		const selectedCount = itemCheckboxes.filter((checkbox) => checkbox.checked).length;
		if (selectionCount) {
			selectionCount.textContent = `(${selectedCount})`;
		}
		if (selectAll) {
			selectAll.checked = itemCheckboxes.length > 0 && selectedCount === itemCheckboxes.length;
			selectAll.indeterminate = selectedCount > 0 && selectedCount < itemCheckboxes.length;
		}
	};

	if (selectAll) {
		selectAll.addEventListener("change", () => {
			itemCheckboxes.forEach((checkbox) => {
				checkbox.checked = selectAll.checked;
			});
			updateSelectionState();
		});
	}
	itemCheckboxes.forEach((checkbox) => checkbox.addEventListener("change", updateSelectionState));
	updateSelectionState();
});
