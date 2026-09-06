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

	const discoveryPlatform = document.querySelector("[data-discovery-platform]");
	if (discoveryPlatform) {
		const form = discoveryPlatform.form;
		const catalogItems = Array.from(form.querySelectorAll("[data-catalog-item]"));
		const selectCatalogs = form.querySelector("[data-catalog-select-all]");
		const syncCatalogs = () => {
			const selected = catalogItems.filter((item) => item.checked).length;
			selectCatalogs.checked = selected === catalogItems.length;
			selectCatalogs.indeterminate = selected > 0 && selected < catalogItems.length;
		};
		selectCatalogs.addEventListener("change", () => {
			catalogItems.forEach((item) => { item.checked = selectCatalogs.checked; });
			syncCatalogs();
		});
		catalogItems.forEach((item) => item.addEventListener("change", syncCatalogs));
		const syncPlatform = () => {
			const catalogs = discoveryPlatform.value === "saatvesaat";
			form.querySelectorAll("[data-catalog-control], [data-trendyol-control]").forEach((group) => {
				const enabled = group.hasAttribute("data-catalog-control") ? catalogs : !catalogs;
				group.hidden = !enabled;
				if (group.tagName === "FIELDSET") group.disabled = !enabled;
				group.querySelectorAll("input").forEach((input) => { input.disabled = !enabled; });
			});
			form.querySelector("[name='brand']").required = !catalogs;
		};
		syncCatalogs();
		syncPlatform();
		discoveryPlatform.addEventListener("change", syncPlatform);
	}
	const discoveryPreview = document.querySelector("[data-discovery-preview]");
	if (discoveryPreview) {
		const filter = discoveryPreview.querySelector("[data-discovery-filter]");
		const rows = Array.from(discoveryPreview.querySelectorAll("[data-discovery-row]"));
		const discoveryItems = Array.from(discoveryPreview.querySelectorAll("[data-discovery-item]:not(:disabled)"));
		const discoverySelectAll = discoveryPreview.querySelector("[data-discovery-select-all]");
		const discoveryCount = discoveryPreview.querySelector("[data-discovery-selection-count]");
		const discoverySubmit = discoveryPreview.querySelector("[data-discovery-submit]");

		const visibleItems = () => discoveryItems.filter((checkbox) => !checkbox.closest("[data-discovery-row]").hidden);
		const updateDiscoverySelection = () => {
			const selectedCount = discoveryItems.filter((checkbox) => checkbox.checked).length;
			const visible = visibleItems();
			const visibleSelected = visible.filter((checkbox) => checkbox.checked).length;
			discoveryCount.textContent = String(selectedCount);
			discoverySubmit.disabled = selectedCount === 0;
			discoverySelectAll.checked = visible.length > 0 && visibleSelected === visible.length;
			discoverySelectAll.indeterminate = visibleSelected > 0 && visibleSelected < visible.length;
		};

		discoverySelectAll.addEventListener("change", () => {
			visibleItems().forEach((checkbox) => {
				checkbox.checked = discoverySelectAll.checked;
			});
			updateDiscoverySelection();
		});
		discoveryItems.forEach((checkbox) => checkbox.addEventListener("change", updateDiscoverySelection));
		filter.addEventListener("input", () => {
			const query = filter.value.trim().toLocaleLowerCase("tr");
			rows.forEach((row) => {
				row.hidden = query.length > 0 && !row.dataset.filterText.includes(query);
			});
			updateDiscoverySelection();
		});
		updateDiscoverySelection();
	}

	document.querySelectorAll("[data-dialog-open]").forEach((button) => {
		button.addEventListener("click", () => {
			const dialog = document.getElementById(button.dataset.dialogOpen);
			if (!dialog || typeof dialog.showModal !== "function") {
				return;
			}
			dialog.showModal();
			const confirmationInput = dialog.querySelector("[data-confirmation-input]");
			if (confirmationInput) {
				confirmationInput.focus();
			}
		});
	});

	document.querySelectorAll("dialog.confirmation-dialog").forEach((dialog) => {
		dialog.querySelectorAll("[data-dialog-cancel]").forEach((button) => {
			button.addEventListener("click", () => dialog.close());
		});
		dialog.addEventListener("click", (event) => {
			if (event.target === dialog) {
				dialog.close();
			}
		});
	});

	document.querySelectorAll("form[data-typed-confirmation]").forEach((form) => {
		const input = form.querySelector("[data-confirmation-input]");
		const submit = form.querySelector("[data-confirmation-submit]");
		const expected = form.dataset.typedConfirmation;
		const updateConfirmationState = () => {
			submit.disabled = input.value !== expected;
		};
		input.addEventListener("input", updateConfirmationState);
		updateConfirmationState();
	});
});
