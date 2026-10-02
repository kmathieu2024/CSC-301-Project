function showPage(pageId, button) {

    const pages = document.querySelectorAll(".page");

    pages.forEach(page => {
        page.classList.remove("active-page");
    });

    document.getElementById(pageId).classList.add("active-page");


    const buttons = document.querySelectorAll(".nav");

    buttons.forEach(btn => {
        btn.classList.remove("active");
    });

    button.classList.add("active");
}
