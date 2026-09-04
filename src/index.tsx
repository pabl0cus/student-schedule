import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './app/App';
import 'normalize.css/normalize.css';
import './index.css';
import { BrowserRouter } from 'react-router-dom';
import dayjs from 'dayjs';
import 'dayjs/locale/uk';

dayjs.locale('uk');

const root = ReactDOM.createRoot(document.getElementById('root') as HTMLElement);
root.render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
