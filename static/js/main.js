document.addEventListener("DOMContentLoaded", () => {
	const shell = document.querySelector("[data-shell]");
	const sidebarToggle = document.querySelector("[data-sidebar-toggle]");
	if (!shell || !sidebarToggle) {
		return;
	}

	sidebarToggle.addEventListener("click", () => {
		const isOpen = shell.classList.toggle("is-sidebar-open");
		sidebarToggle.setAttribute("aria-expanded", String(isOpen));
	});
});
