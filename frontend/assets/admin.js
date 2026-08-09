async function loadUsers() {
  const table = document.getElementById("usersTable");
  if (!table) return;

  try {
    const res = await fetch("/api/users", {
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${localStorage.getItem("tunix_access_token") || ""}`,
      },
    });
    if (!res.ok) throw new Error("Cannot load users");
    const users = await res.json();
    table.innerHTML = users
      .map(
        (u) =>
          `<tr><td>${u.id}</td><td>${u.email}</td><td>${u.full_name}</td><td>${u.role}</td><td>${u.plan}</td></tr>`
      )
      .join("");
  } catch {
    table.innerHTML = `<tr><td colspan="5">Khong tai duoc user list. Dang nhap bang tai khoan admin.</td></tr>`;
  }
}

window.addEventListener("load", loadUsers);
