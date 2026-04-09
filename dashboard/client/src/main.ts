import { createApp } from "vue";
import { createPinia } from "pinia";
import App from "./App.vue";
import "./styles/main.css";
import "./styles/global.css";
import "./styles/command-center.css";
import "highlight.js/styles/github-dark.css";

const app = createApp(App);
app.use(createPinia());
app.mount("#app");
