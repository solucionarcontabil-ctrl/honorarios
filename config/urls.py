from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("entrar/", auth_views.LoginView.as_view(template_name="login.html"), name="login"),
    path("sair/", auth_views.LogoutView.as_view(), name="logout"),
    path("senha/", auth_views.PasswordChangeView.as_view(template_name="cobranca/form.html",
         success_url="/", extra_context={"titulo": "Trocar senha"}), name="trocar_senha"),
    path("", include("obrigacoes.urls")),
    path("", include("xmls.urls")),
    path("", include("portal.urls")),
    path("", include("rentabilidade.urls")),
    path("", include("contabil.urls")),
    path("", include("fiscal.urls")),
    path("", include("cobranca.urls")),
]
