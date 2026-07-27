"""asset_storage.carica_pubblico(): carica un'immagine generata su
Cloudflare R2 (storage S3-compatibile) per soddisfare il requisito di
Instagram (image_url raggiungibile da Internet, mai i byte diretti come
LinkedIn) senza dover esporre l'intera app dietro un dominio pubblico.
Difensivo come le altre integrazioni opzionali: mai un'eccezione, solo
None se non configurato o se l'upload fallisce."""

from unittest import mock

from social import asset_storage


def _pulisci_env_r2(monkeypatch):
    for chiave in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
                  "R2_BUCKET_NAME", "R2_PUBLIC_BASE_URL"):
        monkeypatch.delenv(chiave, raising=False)


def _imposta_env_r2(monkeypatch):
    monkeypatch.setenv("R2_ACCOUNT_ID", "account-test")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "chiave-test")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "segreto-test")
    monkeypatch.setenv("R2_BUCKET_NAME", "bucket-test")
    monkeypatch.setenv("R2_PUBLIC_BASE_URL", "https://immagini.jobinpa.it")


def test_carica_pubblico_ritorna_none_se_non_configurato(monkeypatch, tmp_path):
    _pulisci_env_r2(monkeypatch)
    file_test = tmp_path / "immagine.png"
    file_test.write_bytes(b"contenuto finto")
    assert asset_storage.carica_pubblico(str(file_test)) is None


def test_carica_pubblico_chiama_put_object_e_ritorna_url(monkeypatch, tmp_path):
    _imposta_env_r2(monkeypatch)
    file_test = tmp_path / "immagine.png"
    file_test.write_bytes(b"contenuto finto")

    client_finto = mock.Mock()
    with mock.patch("boto3.client", return_value=client_finto) as crea_client:
        url = asset_storage.carica_pubblico(str(file_test))

    assert url is not None
    assert url.startswith("https://immagini.jobinpa.it/")
    assert url.endswith("-immagine.png")
    crea_client.assert_called_once()
    _, kwargs = crea_client.call_args
    assert kwargs["endpoint_url"] == "https://account-test.r2.cloudflarestorage.com"
    client_finto.put_object.assert_called_once()
    _, put_kwargs = client_finto.put_object.call_args
    assert put_kwargs["Bucket"] == "bucket-test"
    assert put_kwargs["ContentType"] == "image/png"


def test_carica_pubblico_ritorna_none_se_upload_fallisce(monkeypatch, tmp_path):
    _imposta_env_r2(monkeypatch)
    file_test = tmp_path / "immagine.png"
    file_test.write_bytes(b"contenuto finto")

    client_finto = mock.Mock()
    client_finto.put_object.side_effect = Exception("errore di rete finto")
    with mock.patch("boto3.client", return_value=client_finto):
        assert asset_storage.carica_pubblico(str(file_test)) is None


def test_carica_pubblico_file_inesistente_ritorna_none(monkeypatch):
    _imposta_env_r2(monkeypatch)
    assert asset_storage.carica_pubblico("/percorso/che/non/esiste.png") is None


def test_visual_persiste_url_pubblico_su_ogni_asset(conn, monkeypatch):
    """agents.visual() deve caricare ogni immagine generata (anche quelle
    del carosello) e salvare l'URL pubblico risultante sull'asset."""
    from social import agents, db_social, llm, models
    from social.images import MockImageProvider

    import os
    monkeypatch.setattr(agents.asset_storage, "carica_pubblico",
                        lambda percorso: f"https://cdn.test/{os.path.basename(str(percorso))}")
    content_id = db_social.crea_content(conn, "Prova upload", canali=["instagram"])
    risultato = models.RisultatoRicerca(fatti=[], sintesi="")
    agents.visual(conn, content_id, risultato, provider=llm.MockLLMProvider(conn),
                  image_provider=MockImageProvider())
    asset = db_social.asset_di(conn, content_id)
    assert len(asset) == 1
    assert asset[0]["url_pubblico"] is not None
    assert asset[0]["url_pubblico"].startswith("https://cdn.test/")
