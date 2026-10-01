document.addEventListener("DOMContentLoaded", function () {
    // Copy the student attendance link so the lecturer can share it manually.
    const copyButton = document.querySelector("[data-copy-target]");
    if (copyButton) {
        copyButton.addEventListener("click", async function () {
            const target = document.querySelector(copyButton.dataset.copyTarget);
            if (!target) {
                return;
            }

            try {
                await navigator.clipboard.writeText(target.value);
                copyButton.textContent = "Copied";
                setTimeout(() => {
                    copyButton.textContent = "Copy Link";
                }, 1600);
            } catch {
                target.select();
                document.execCommand("copy");
            }
        });
    }

    // Confirm attendance before a student submits the form.
    const form = document.querySelector("form");

    if (form && form.dataset.confirmAttendance === "true") {
        form.addEventListener("submit", function (e) {

            const name = document.querySelector("input[name='name']").value.trim();
            const studentId = document.querySelector("input[name='student_id']").value.trim();

            if (name === "" || studentId === "") {
                alert("Please fill in all fields!");
                e.preventDefault();
                return;
            }

            const confirmSubmit = confirm("Submit attendance?");
            if (!confirmSubmit) {
                e.preventDefault();
            }
        });
    }

    // Confirm delete actions before removing sessions or courses.
    document.querySelectorAll("[data-confirm-delete='true']").forEach(function (deleteForm) {
        deleteForm.addEventListener("submit", function (e) {
            const confirmed = confirm("Are you sure you want to delete this item?");
            if (!confirmed) {
                e.preventDefault();
            }
        });
    });
});
